"""Converts extracted JSON data to more useful formats"""

import asyncio
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import aiofiles
import aiofiles.os
import pydantic
import pydantic_core
import shapely
from tqdm.auto import tqdm

from .utils import read_text, write_json, write_text

if TYPE_CHECKING:
	from shapely.geometry.base import BaseGeometry

logger = logging.getLogger(__name__)

# innermost sequence should be just two values (lat and lng)
PolygonCoordinates = Sequence[Sequence[Sequence[float]]]


# if you just have this as pydantic.RootModel[dict[str, PolygonCoordinates]] then linters complain if you use isinstance
class PolygonCoordinatesDict(pydantic.RootModel):
	root: dict[str, PolygonCoordinates]


class Coordinate(pydantic.BaseModel, extra='forbid'):
	lat: float
	lng: float


class BoundingBox(pydantic.BaseModel, extra='forbid'):
	NW: Coordinate
	NE: Coordinate
	SE: Coordinate
	SW: Coordinate


class BoundingBoxDict(pydantic.RootModel):
	root: dict[str, BoundingBox]

#hmm, more specifically it is _usually_ just an <svg> element by itself, but can include an XML declaration/comments/other things that thou shalt not parse via regex
SVGStr = Annotated[
	str, pydantic.StringConstraints(pattern=re.compile(r'<svg.+</svg>\s*$', re.DOTALL))
]


class SVGDict(pydantic.RootModel):
	root: dict[str, SVGStr]

#hmmmmmm
RawSVGPath = Annotated[str, pydantic.StringConstraints(pattern=r'^M\d.+\dZ?$')]


class RawSVGDict(pydantic.RootModel):
	root: dict[str, RawSVGPath]


ConvertableDictAdapter: pydantic.TypeAdapter[
	PolygonCoordinatesDict | BoundingBoxDict | SVGDict | RawSVGDict
] = pydantic.TypeAdapter(PolygonCoordinatesDict | BoundingBoxDict | SVGDict | RawSVGDict)


def polygons_to_actual_polygon(coords: PolygonCoordinates) -> shapely.MultiPolygon:
	# Okay so bad news: We can't really tell the difference between an exterior and interior here, and the code is too obfuscated to see what it's doing with that data
	# Okay maybe… this
	rings = [shapely.LinearRing(points) for points in coords]
	polys = []
	for i in range(0, len(rings), 2):
		ring = rings[i]
		poly = shapely.Polygon(ring)
		if i + 1 == len(rings):
			polys.append(poly)
		else:
			next_ring = rings[i + 1]
			if poly.contains(next_ring):
				polys.append(shapely.Polygon(ring, [next_ring]))
			else:
				polys.extend((poly, shapely.Polygon(next_ring)))

	return shapely.MultiPolygon(polys)


def to_geojson(name: str, features: Sequence[tuple['BaseGeometry', Mapping[str, Any]]]):
	return {
		'type': 'FeatureCollection',
		'name': name,
		'crs': {'type': 'name', 'properties': {'name': 'urn:ogc:def:crs:OGC:1.3:CRS84'}},
		'features': [
			{'type': 'Feature', 'properties': properties, 'geometry': geometry.__geo_interface__}
			for geometry, properties in features
		],
	}


async def convert_polygon(data: PolygonCoordinatesDict, path: Path, out_dir: Path):
	# index is usually a lowercase alpha-2 country code, but might not be
	features = [
		(polygons_to_actual_polygon(coords), {'index': index})
		for index, coords in data.root.items()
	]
	geojson = to_geojson(path.name, features)
	out_path = out_dir / path.with_suffix('.geojson').name
	await write_json(out_path, geojson)


async def convert_box(data: BoundingBoxDict, path: Path, out_dir: Path):
	features = [
		(shapely.Polygon([[corner.lng, corner.lat] for _, corner in box]), {'index': index})
		for index, box in data.root.items()
	]
	geojson = to_geojson(path.name, features)
	out_path = out_dir / path.with_suffix('.geojson').name
	await write_json(out_path, geojson)


async def convert_multiple_svg(data: SVGDict, path: Path, out_dir: Path):
	out_dir /= path.stem
	await aiofiles.os.makedirs(out_dir, exist_ok=True)
	for index, svg in data.root.items():
		svg_path = out_dir / f'{index}.svg'
		await write_text(svg_path, svg)


async def convert_raw_svg(data: RawSVGDict, path: Path, out_dir: Path):
	d = {}
	# This is just what the explorer map uses
	svg_start = '<svg width="820" height="520" viewBox="30 40 800 480">\n'
	for index, svg_path in data.root.items():
		d[index] = f'<path id="{index}" d="{svg_path}"/>'

	svg_content = svg_start + '\n\t'.join(d.values()) + '\n</svg>'
	out_path = out_dir / f'{path.stem}.svg'
	await write_text(out_path, svg_content)


async def convert_file(path: Path, out_dir: Path):
	content = await read_text(path)

	try:
		data = ConvertableDictAdapter.validate_json(content)
	except pydantic.ValidationError:
		# Just want to see what it looks like, not necessarily the whole thing
		try:
			raw = pydantic_core.from_json(content)
			if isinstance(raw, dict):
				raw = raw.popitem()
				if isinstance(raw[0], str) and isinstance(raw[1], str):
					#not really anything special/interesting
					return
			logger.info('Could not convert %s, unknown type: %s', path, raw)
		except ValueError:
			logger.info('Could not convert %s, not even JSON: %s', path, content)
		return

	if isinstance(data, PolygonCoordinatesDict):
		await convert_polygon(data, path, out_dir / 'Polygons')
	elif isinstance(data, BoundingBoxDict):
		await convert_box(data, path, out_dir / 'Bounding boxes')
	elif isinstance(data, SVGDict):
		# probably flags of some kind
		await convert_multiple_svg(data, path, out_dir / 'SVGs')
	elif isinstance(data, RawSVGDict):
		await convert_raw_svg(data, path, out_dir / 'SVGs')


async def convert_all_files(paths: Iterable[Path], out_dir: Path):
	fs = [convert_file(path, out_dir) for path in paths]
	with tqdm(asyncio.as_completed(fs), total=len(fs), desc='Converting', unit='file') as t:
		for result in t:
			await result
