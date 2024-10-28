import asyncio
import json
import shutil
from collections.abc import Collection, Mapping, Sequence
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any, TypeVar

import aiofiles
import aiofiles.os
import aiofiles.ospath
from tqdm.auto import tqdm

from .typedefs import URLPath

if TYPE_CHECKING:
	import aiohttp

	from .typedefs import URL, JSONData


KT = TypeVar('KT')
VT = TypeVar('VT')


def reverse_dict_of_lists(d: Mapping[KT, Collection[VT]]) -> Mapping[VT, Sequence[KT]]:
	out: dict[VT, list[KT]] = {}
	for k, v in d.items():
		for vv in v:
			_list = out.setdefault(vv, [])
			if k not in _list:
				_list.append(k)
	return out


async def read_text(path: Path, encoding: str = 'utf-8'):
	#yeah I really just didn't feel like typing this out every time, whatevs
	async with aiofiles.open(path, encoding=encoding) as f:
		return await f.read()


async def write_text(path: Path, s: str, encoding: str = 'utf-8', errors: str='strict'):
	async with aiofiles.open(path, 'w', encoding=encoding, errors=errors) as f:
		await f.write(s)


def json_default(o: Any):
	if isinstance(o, PurePath):
		return str(o)
	if isinstance(o, (set, frozenset)):
		return list(o)
	return o


async def write_json(path: Path, data: 'JSONData', *, sort_keys: bool = False):
	await aiofiles.os.makedirs(path.parent, exist_ok=True)
	j = json.dumps(data, indent='\t', ensure_ascii=False, sort_keys=sort_keys, default=json_default)
	await write_text(path, j)


async def _read_response_with_progress(response: 'aiohttp.ClientResponse', **tqdm_kwargs):
	bytesio = BytesIO()
	content_length_str = response.headers.get('Content-Length')
	content_length = int(content_length_str) if content_length_str else float('inf')
	with tqdm.wrapattr(
		bytesio,
		'write',
		unit='iB',
		unit_scale=True,
		unit_divisor=1024,
		total=content_length,
		disable=content_length < 1024,
		**tqdm_kwargs,
	) as b:
		async for chunk, _ in response.content.iter_chunks():
			b.write(chunk)
	return bytesio.getvalue()


async def get_binary_file(
	session: 'aiohttp.ClientSession',
	url: 'URL',
	semaphore: asyncio.Semaphore | None = None,
	*,
	progress: bool = True,
) -> bytes:
	async with nullcontext() if semaphore is None else semaphore, session.get(url) as response:
		response.raise_for_status()
		if not progress:
			return await response.content.read()
		return await _read_response_with_progress(response, desc=url, leave=False)


async def download_binary_file(
	static_url: str,
	out_path: Path,
	session: 'aiohttp.ClientSession',
	semaphore: asyncio.Semaphore | None = None,
	*,
	progress: bool = True,
):
	await aiofiles.os.makedirs(out_path.parent, exist_ok=True)
	data = await get_binary_file(session, static_url, semaphore, progress=progress)
	async with aiofiles.open(out_path, 'wb') as f:
		await f.write(data)


async def get_text(
	session: 'aiohttp.ClientSession', url: 'URL | URLPath', *, progress: bool = True
):
	if isinstance(url, URLPath):
		url = f'https://www.geoguessr.com/{url}'
	async with session.get(url) as response:
		response.raise_for_status()
		if not progress:
			return await response.text()
		content = await _read_response_with_progress(response, desc=url, leave=False)
		return content.decode('utf-8')


async def deltree_if_exists(path: Path):
	if not await aiofiles.ospath.isdir(path):
		return
	await asyncio.to_thread(shutil.rmtree, path)


def abbrev_path(path: Path, root_dir: Path):
	return str(path.relative_to(root_dir) if path.is_relative_to(root_dir) else path)
