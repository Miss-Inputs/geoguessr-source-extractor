import asyncio
import contextlib
import itertools
import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles
import aiofiles.os
import jsbeautifier
from aiohttp import ClientResponseError
from tqdm.auto import tqdm

from .build_manifest import parse_build_manifest
from .find_urls import FoundURLs, find_urls_from_home_page
from .utils import get_text
from .webpack import parse_webpack

if TYPE_CHECKING:
	import aiohttp

	from .typedefs import URLPath

logger = logging.getLogger(__name__)


async def _download_with_url(
	session: 'aiohttp.ClientSession', url: 'URLPath', semaphore: asyncio.Semaphore | None = None
) -> tuple['URLPath', str]:
	async with contextlib.nullcontext() if semaphore is None else semaphore:
		return url, await get_text(session, url)


@dataclass
class DiscoveredFiles:
	build_manifest: Mapping['URLPath', Collection['URLPath']]
	"""Parsed _buildManifest.js, mapping page routes to used source files"""
	build_id: str
	files: Collection['URLPath']
	"""All discovered source files"""
	urls: FoundURLs


async def discover_files(session: 'aiohttp.ClientSession') -> DiscoveredFiles:
	"""Attempts to discover as many source files as it can using the build manifest, webpack, etc.

	Returns:
		(Parsed build manifest, build ID, set of URLPath (relative to website root))"""
	urls = await find_urls_from_home_page(session)

	build_manifest_source = await get_text(session, urls.build_manifest)
	build_id = urls.build_manifest.parent.name
	build_manifest = parse_build_manifest(build_manifest_source)
	# pprint(manifest)
	# Pages can contain the same chunk, so you wouldn't want to just end up creating a folder for each chunk
	all_chunks = set(itertools.chain.from_iterable(build_manifest.values()))
	all_chunks.add(urls.build_manifest)
	all_chunks.add(urls.webpack)
	all_chunks.add(urls.app)
	all_chunks.update(urls.other_urls)

	# Webpack might have some stuff that isn't in the build manifest too
	webpack = await get_text(session, urls.webpack)
	all_chunks.update(parse_webpack(webpack))

	return DiscoveredFiles(build_manifest, build_id, all_chunks, urls)


async def download_source(
	session: 'aiohttp.ClientSession', website_source_dir: Path, max_connections: int = 1
):
	"""Discovers GeoGuessr source and downloads to a directory.

	Returns:
		(DiscoveredFiles, set of all local paths that we downloaded)"""
	# getPageList() {
	#   return (0, f.getClientBuildManifest) ().then(e => e.sortedPages)
	# }
	#
	files = await discover_files(session)

	semaphore = asyncio.Semaphore(max_connections)
	futures = [_download_with_url(session, chunk_url, semaphore) for chunk_url in files.files]
	out_paths: set[Path] = set()
	with tqdm(
		asyncio.as_completed(futures),
		desc='Downloading source files',
		unit='file',
		total=len(futures),
	) as t:
		for result in t:
			try:
				chunk_url, chunk = await result
			except ClientResponseError as e:
				# This can happen for some files, don't panic
				logger.error('Error: %s', e)
				continue
			t.set_postfix(url=chunk_url)

			out_path = website_source_dir / chunk_url
			chunk_dir = out_path.parent
			await aiofiles.os.makedirs(chunk_dir, exist_ok=True)

			if out_path.suffix == '.js':
				# We are using jsbeautifier to parse JavaScript anyway, so why not use it for its actual intended purpose too?
				# (Well also given the whole point is to poke around in GeoGuessr to see what it does, we really should)
				chunk = jsbeautifier.beautify(chunk, {'indent_with_tabs': True})  # type: ignore[argument]

			async with aiofiles.open(out_path, 'w', encoding='utf8') as f:
				await f.write(chunk)
			out_paths.add(out_path)

	return files, out_paths
