"""Finds the build manifest/webpack/etc URL from the home page, as they are likely different each time."""

import warnings
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup, GuessedAtParserWarning, Tag

if TYPE_CHECKING:
	import aiohttp

	from .typedefs import URLPath


async def get_home_page(session: 'aiohttp.ClientSession'):
	async with session.get('https://www.geoguessr.com') as response:
		html = await response.text()
		with warnings.catch_warnings(action='ignore', category=GuessedAtParserWarning):
			return BeautifulSoup(html)


@dataclass
class FoundURLs:
	build_manifest: 'URLPath'
	webpack: 'URLPath'
	app: 'URLPath'
	other_urls: Collection['URLPath']


class UnexpectedWebpageStructureError(AssertionError):
	"""Something has gone very wrong, and the GeoGuessr home page does not look like how we expect it"""


def find_urls_in_soup(soup: BeautifulSoup):
	head = soup.find('head')
	if not isinstance(head, Tag):
		raise UnexpectedWebpageStructureError(f'<head> element is {type(head)}, expected Tag')

	build_manifest = webpack = app = None
	other_urls = set()
	for script in head.find_all('script'):
		if not isinstance(script, Tag):
			continue
		src = script.get('src')
		if not src:
			continue

		for s in src if isinstance(src, list) else (src,):
			src_path = PurePosixPath(s.removeprefix('/'))
			if src_path.name == '_buildManifest.js':
				# The parent of this path is why we have this, because it changes regularly (looks like this is a build ID from next.js)
				build_manifest = src_path
			elif (
				src_path.parent.name == 'chunks'
				and src_path.stem.startswith('webpack-')
				and src_path.suffix == '.js'
			):
				webpack = src_path
			elif (
				src_path.parent.name == 'pages'
				and src_path.stem.startswith('_app-')
				and src_path.suffix == '.js'
			):
				app = src_path
			else:
				other_urls.add(src_path)

	if not build_manifest:
		raise UnexpectedWebpageStructureError(
			'Unfortunately, build manifest could not be found, and we need that'
		)
	if not webpack:
		raise UnexpectedWebpageStructureError(
			'Unfortunately, webpack was not found, and we probably need that'
		)
	if not app:
		raise UnexpectedWebpageStructureError(
			'Main app JS was not found, but it should be on the home page'
		)
	return FoundURLs(build_manifest, webpack, app, other_urls)


async def find_urls_from_home_page(session: 'aiohttp.ClientSession'):
	soup = await get_home_page(session)
	return find_urls_in_soup(soup)
