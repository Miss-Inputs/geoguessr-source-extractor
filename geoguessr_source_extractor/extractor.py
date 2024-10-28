import asyncio
import contextlib
import itertools
import logging
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PurePosixPath
from typing import TYPE_CHECKING, Any

import aiofiles
import aiofiles.os
import aiofiles.ospath
import aiohttp
from tqdm.auto import tqdm

from .app import parse_localizations_from_app
from .convert import convert_all_files
from .download_source import DiscoveredFiles, download_source
from .interesting_things import APIFunction, InterestingThings, find_interesting_things
from .utils import (
	abbrev_path,
	deltree_if_exists,
	download_binary_file,
	read_text,
	reverse_dict_of_lists,
	write_json,
)

if TYPE_CHECKING:
	from .typedefs import URL, FunctionID, JSONData, ModuleID, URLPath

logger = logging.getLogger(__name__)


@dataclass
class InterestingThingsInAllFiles:
	"""interesting_things.InterestingThings, but across all files"""

	static_urls: dict[Path, Collection['URLPath']] = field(default_factory=dict)
	api_functions: dict[Path, Collection[APIFunction]] = field(default_factory=dict)
	other_api_urls: dict[Path, Collection['URL']] = field(default_factory=dict)
	jsons: dict[Path, Mapping['FunctionID', 'JSONData']] = field(default_factory=dict)
	other_urls: dict[Path, Collection['URL']] = field(default_factory=dict)

	def combine(self, path: Path, things: InterestingThings):
		if things.static_urls:
			self.static_urls[path] = things.static_urls
		if things.api_functions:
			self.api_functions[path] = things.api_functions
		if things.other_api_urls:
			self.other_api_urls[path] = things.other_api_urls
		if things.jsons:
			self.jsons[path] = things.jsons
		if things.other_urls:
			self.other_urls[path] = things.other_urls


async def _find_interesting_things_in_file(file: Path, root_dir: Path):
	relative_path = abbrev_path(file, root_dir)
	return file, await find_interesting_things(file, relative_path)


async def find_interesting_things_in_all_files(
	website_source_dir: Path, source_files: Iterable[Path]
):
	futures = [
		_find_interesting_things_in_file(file, website_source_dir)
		for file in source_files
		if file.suffix == '.js'
	]

	results = InterestingThingsInAllFiles()

	with tqdm(
		asyncio.as_completed(futures),
		desc='Looking for interesting things',
		unit='file',
		total=len(futures),
	) as t:
		for result in t:
			file, things = await result
			t.set_postfix(file=file)
			results.combine(file, things)

	return results


def _is_downloaded_file(path: Path, name_pattern: str, website_source_dir: Path):
	if not path.is_relative_to(website_source_dir):
		return False
	return path.match(name_pattern)


async def _extract_app(
	extracted_data_path: Path,
	downloaded_paths: Iterable[Path],
	website_source_dir: Path,
	app_url_path: PurePath | None,
):
	if not app_url_path:
		app_url_path = next(
			(
				path
				for path in downloaded_paths
				if _is_downloaded_file(
					path, '_next/static/chunks/pages/_app-*.js', website_source_dir
				)
			),
			None,
		)
	if app_url_path:
		app_path = website_source_dir / app_url_path
		localized_data = parse_localizations_from_app(await read_text(app_path))
		await write_json(
			extracted_data_path / 'Localized data file names.json',
			{str(v): f'{k[0]}/{k[1]}' for k, v in localized_data.items()},
			sort_keys=True,
		)
		return localized_data
	logger.warning(
		'_app.js has gone missing since we last saw it, so you will be missing better filenames for localized data'
	)
	return None


async def _dump_api_functions(
	api_functions: Mapping[Path, Collection[APIFunction]], website_source_dir: Path, out_dir: Path
):
	api_funcs_by_name: dict[str, dict[str, Any]] = {}
	# Whoops!! The name of the function is actually not necessarily unique
	for p, funcs in api_functions.items():
		for func in funcs:
			key = f'{func.url} ({func.method}): {func.name}'
			if key in api_funcs_by_name:
				api_funcs_by_name[key]['used_in'].append(abbrev_path(p, website_source_dir))
			else:
				api_funcs_by_name[key] = {
					'args': func.args,
					#JSON would just have literal escaped tabs and newlines and that wouldn't look great
					'body': func.body.replace('\t', ' ').replace('\n', ' '),
					'used_in': [abbrev_path(p, website_source_dir)],
				}

	await write_json(out_dir / 'API functions.json', api_funcs_by_name, sort_keys=True)


async def _dump_static_urls(static_urls: Mapping[str, Collection[str]], out_dir: Path):
	await write_json(out_dir / 'Static URLs.json', static_urls, sort_keys=True)
	await write_json(
		out_dir / 'Static URLs by usage.json', reverse_dict_of_lists(static_urls), sort_keys=True
	)


async def _dump_json_data(
	jsons: Mapping[Path, Mapping['FunctionID', 'JSONData']],
	localized_data: Mapping[tuple['ModuleID', 'FunctionID'], PurePosixPath] | None,
	out_dir: Path,
):
	futures = []
	for path, json_dict in jsons.items():
		module_name = path.stem.split('.', 1)[0].split('-', 1)[0]
		for function_id, json_data in json_dict.items():
			out_name = (
				f'{function_id}.json'
				if module_name == str(function_id)
				else f'{module_name}-{function_id}.json'
			)
			if localized_data:
				with contextlib.suppress(ValueError):
					module_id = int(module_name)
					out_name = localized_data.get((module_id, function_id), out_name)
			out_path = out_dir / 'JSONs' / out_name
			futures.append(write_json(out_path, json_data))

	for result in asyncio.as_completed(futures):
		await result


async def extract_source(
	extracted_data_dir: Path,
	website_source_dir: Path,
	downloaded_paths: Iterable[Path],
	app_url_path: PurePath | None,
	session: aiohttp.ClientSession,
	*,
	download_static_files: bool = False,
	force_redownload_static_files: bool = False,
):
	# There is just js and css in there
	await aiofiles.os.makedirs(extracted_data_dir, exist_ok=True)

	localized_data = await _extract_app(
		extracted_data_dir, downloaded_paths, website_source_dir, app_url_path
	)

	interesting_things = await find_interesting_things_in_all_files(
		website_source_dir, downloaded_paths
	)

	# Getting real sick of this "keys can only be str/bool/int/blah" nonsense
	static_urls = {
		abbrev_path(p, website_source_dir): [str(p) for p in v]
		for p, v in interesting_things.static_urls.items()
	}
	for result in asyncio.as_completed(
		(
			_dump_api_functions(
				interesting_things.api_functions, website_source_dir, extracted_data_dir
			),
			_dump_static_urls(static_urls, extracted_data_dir),
			write_json(
				extracted_data_dir / 'API URLs.json',
				reverse_dict_of_lists(
					{
						abbrev_path(k, website_source_dir): v
						for k, v in interesting_things.other_api_urls.items()
					}
				),
				sort_keys=True,
			),
			_dump_json_data(interesting_things.jsons, localized_data, extracted_data_dir),
			write_json(
				extracted_data_dir / 'Other URLs.json',
				reverse_dict_of_lists(
					{
						abbrev_path(k, website_source_dir): v
						for k, v in interesting_things.other_urls.items()
					}
				),
				sort_keys=True,
			),
		)
	):
		await result

	if not download_static_files:
		return

	all_static_urls = frozenset(
		itertools.chain.from_iterable(interesting_things.static_urls.values())
	)
	paths_to_download: dict[URL, Path] = {}
	for static_url in all_static_urls:
		out_path = website_source_dir / static_url
		if force_redownload_static_files or not await aiofiles.ospath.isfile(out_path):
			paths_to_download[f'http://www.geoguessr.com/{static_url}'] = out_path

	if paths_to_download:
		semaphore = asyncio.Semaphore(1)
		futures = [
			download_binary_file(static_url, out_path, session, semaphore)
			for static_url, out_path in paths_to_download.items()
		]
		for result in tqdm.as_completed(futures, desc='Downloading static files'):
			await result


async def dump_build_manifest(files: DiscoveredFiles, extracted_data_dir: Path):
	build_manifest = {str(k): [str(p) for p in v] for k, v in files.build_manifest.items()}
	build_manifest_path = extracted_data_dir / f'Build manifest ({files.build_id}).json'
	await write_json(build_manifest_path, build_manifest, sort_keys=True)

	build_manifest_by_page_path = (
		extracted_data_dir / f'Build manifest by page ({files.build_id}).json'
	)
	build_manifest_by_page = reverse_dict_of_lists(build_manifest)
	await write_json(build_manifest_by_page_path, build_manifest_by_page, sort_keys=True)


async def download_and_extract(
	website_source_dir: Path,
	extracted_data_dir: Path,
	max_connections: int = 1,
	*,
	force_redownload: bool = False,
	download_static_files: bool = True,
	force_redownload_static_files: bool = False,
):
	"""The main entry point of sorts."""
	session = aiohttp.ClientSession()
	# Probably a good idea?
	session.headers['User-Agent'] = (
		'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
	)

	async with session:
		if force_redownload or not await aiofiles.ospath.isdir(website_source_dir):
			await deltree_if_exists(website_source_dir)
			files, downloaded_paths = await download_source(
				session, website_source_dir, max_connections
			)
			await dump_build_manifest(files, extracted_data_dir)
			app_path = files.urls.app
		else:
			downloaded_paths = frozenset(website_source_dir.rglob('*'))
			app_path = None

		await extract_source(
			extracted_data_dir,
			website_source_dir,
			downloaded_paths,
			app_path,
			session,
			download_static_files=download_static_files,
			force_redownload_static_files=force_redownload_static_files,
		)

	await convert_all_files(extracted_data_dir.glob('JSONs/*.json'), extracted_data_dir)
