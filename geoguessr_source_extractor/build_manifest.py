from collections.abc import Mapping
from typing import TYPE_CHECKING

from .tokenize import tokenize_js
from .typedefs import URLPath

if TYPE_CHECKING:
	from .typedefs import JSSource


def _convert_path(p: str):
	"""Paths in _buildManifest are relative to /_next/ and not to root, so this normalizes them to what we expect"""
	return URLPath('_next/') / p


def parse_build_manifest(build_manifest: 'JSSource') -> Mapping[URLPath, list[URLPath]]:
	"""Parses _buildManifest.js, resolving the arguments it passes to itself, etc.

	Returns:
		{/page route}: [list of .js/.css files used by that page]"""
	tokens = tokenize_js(build_manifest, {'unescape_strings': True})
	build_manifest_token = next(
		t for t in tokens if t.type == 'TK_WORD' and t.text == '__BUILD_MANIFEST'
	)
	assert build_manifest_token.next
	assert build_manifest_token.next.next
	function_args_list_start = (
		build_manifest_token.next.next.next
	)  # next = TK_OPERATOR =, next.next = TK_RESERVED function
	variables = [
		t.text for t in tokens if t.parent == function_args_list_start and t.type == 'TK_WORD'
	]

	function_body_start = function_args_list_start.closed.next
	function_args_start = function_body_start.closed.next
	args = [
		t.text.strip('"')
		for t in tokens
		if t.parent == function_args_start and t.type == 'TK_STRING'
	]

	return_token = next(
		t
		for t in tokens
		if t.parent == function_body_start and t.type == 'TK_RESERVED' and t.text == 'return'
	)
	return_dict_start = return_token.next

	d: dict[URLPath, list[URLPath]] = {}
	# print([token.text for token in tokens if token.parent == return_dict_start and token.type == 'TK_WORD'])
	# There are also some TK_WORD tokens in this dict: __rewrites, sortedPages
	return_tokens = [
		token for token in tokens if token.parent == return_dict_start and token.type == 'TK_STRING'
	]
	for token in return_tokens:
		key: str = token.text.strip('"')
		assert token.next, 'token.next should be TK_OPERATOR : but it was None'
		start = token.next.next
		children = [t for t in tokens if t.parent == start and t.type != 'TK_COMMA']
		d[URLPath(key)] = [
			_convert_path(c.text.strip('"') if c.type == 'TK_STRING' else args[variables.index(c.text)])
			for c in children
		]
	return d
