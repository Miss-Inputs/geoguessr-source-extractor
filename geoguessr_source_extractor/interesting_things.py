import json
import logging
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pydantic_core

from .tokenize import describe_token, tokenize_js
from .typedefs import URLPath
from .utils import read_text

if TYPE_CHECKING:
	from jsbeautifier.core.token import Token

	from .typedefs import URL, FunctionID, JSONData, JSONSource, JSSource

logger = logging.getLogger(__name__)


class NotParseableError(Exception):
	"""We found this isn't actually something we can parse"""


class UnexpectedParseError(NotParseableError):
	"""Something happened in parsing which we didn't expect, which means we can't parse it, but it might be worth warning about"""


def _convert_hex_escape(match: re.Match[str]):
	char_code = int(match[1] or match[2], 16)
	if 0xD800 <= char_code <= 0xDFFF:
		# Surrogates, which are going to cause problems if we let that happen, and also are not valid UTF-8 so we should probably just leave that as a hex escape and let pydantic_core parse it
		return f'\\{match[0]}'
	return chr(char_code)


def _unescape_and_parse_json(text: 'JSONSource', path_for_log: Any = 'This string'):
	"""The contents of a literal string passed to JSON.parse is not always exactly usable as JSON directly, because of escaping"""
	text = re.sub(r'\\x([\dA-Fa-f]{2})|\\u([\dA-Fa-f]{4})', _convert_hex_escape, text)
	text = re.sub(r'\\(.)', r'\1', text)
	try:
		return pydantic_core.from_json(text)
	except TypeError:
		#This shouldn't happen anymore, but just in case
		logger.exception("wat? %s isn't stringy enough for pydantic_core", path_for_log)
		return json.loads(text)


def parse_json_literal(
	json_token: 'Token', parse_token: 'Token', path_for_log: Any | None = None
) -> tuple['FunctionID', 'JSONData']:
	"""Parses specific GeoGuessr JSON embedded in something like <blah>.exports = JSON.parse("blah")

	Arguments:
		json_token: Parsed token representing "JSON"
		parse_token: Parsed token representing "parse"

	Raises:
		NotParseableError: If this is not something we can simply parse
		UnexpectedParseError: If this is not something we can simply parse, but it looked like it was

	Returns:
		(module ID, parsed object from JSON)
	"""
	# Lots of things to ignore here, as Pyright will interpret jsbeautifier's Token class as just having None for all attributes
	json_parse_start: Token = parse_token.next  # type: ignore[assignment]
	json_parse_arg: Token = json_parse_start.next  # type: ignore[assignment]
	if json_parse_arg.type != 'TK_STRING':
		# We can only parse strings, so raise an error with everything which is an argument to JSON.parse
		blah = ''
		blahblah = json_parse_start.next
		while blahblah != json_parse_start.closed:
			if blahblah is None:
				break
			blah += blahblah.text
			blahblah = blahblah.next
		raise NotParseableError(
			f'Cannot parse JSON in {path_for_log}, because it has variables: {blah}'
		)
	if json_parse_arg.next != json_parse_start.closed:
		raise NotParseableError(
			f'Cannot parse JSON in {path_for_log}, because it has more than one argument: {describe_token(json_parse_arg)}'
		)
	json_raw = json_parse_arg.text.strip('"\'')
	j = _unescape_and_parse_json(json_raw, path_for_log)

	# TODO: This only parses certain jsons, see also _next/static/chunks/57df6379-48b36665020add46.js for example, which has it start with "let l = " instead (but that in particular is probabably not something we need to worry about)

	function_body_start: Token = json_token.parent  # type: ignore[assignment]
	function_args_end: Token = function_body_start.previous  # type: ignore[assignment]
	function_args_begin: Token | None = function_args_end.opened
	if not function_args_begin:
		raise UnexpectedParseError(
			f'JSON in {path_for_log} not how we expected it, function_args_begin is None'
		)
	function_keyword: Token = function_args_begin.previous
	colon: Token = function_keyword.previous
	key: Token = colon.previous
	# json_token.previous.previous == 'exports'
	# if json_token.previous.type == 'TK_EQUALS' and json_token.previous.text == '=':
	# 	print(key.text, 'assigned to', json_token.previous.previous.text)
	# Handle "14e3" or whatever weird shit
	return int(float(key.text)), j


@dataclass
class APIFunction:
	"""A detected reference to an API endpoint in the JavaScript code."""

	name: str
	"""JavaScript function name, which may give us clues to what purpose this serves"""
	args: list[str]
	"""Names of argumetnts to JavaScript function, if any"""
	body: 'JSSource'
	"""JavaScript function body, which may help figure out how arguments are used"""
	url: 'URL'
	"""The actual API endpoint (without host)"""
	method: str
	""""get", "post", etc"""


def parse_api_url(token: 'Token', other_tokens: Iterable['Token']) -> 'APIFunction | URL':
	method_args_start: Token = (
		token.parent
	)  # d.Mb.get, or perhaps d.Mb.post, etc #type: ignore[assignment]
	# TODO: If call, yoink the first argument instead
	try_block_start: Token = method_args_start.parent  # type: ignore[assignment]
	if try_block_start.previous.text == 'try':  # type: ignore[attr]
		function_def_start: Token = try_block_start.parent  # type: ignore[assignment]
		body_token = function_def_start
		body = ''
		while body_token != function_def_start.closed:
			body += ('\n' * body_token.newlines) + body_token.whitespace_before + body_token.text  # type: ignore[attr]
			body_token = body_token.next  # type: ignore[attr]

		function_args_end: Token = function_def_start.previous  # type: ignore[assignment]
		function_args_begin = function_args_end.opened
		function_name = function_args_begin.previous  # type: ignore[attr]
		# blah.Nw = encodeUriComponent
		# blah.Mb = geoguessr.com
		return APIFunction(
			function_name.text,
			[
				t.text
				for t in other_tokens
				if t.parent == function_args_begin and t.type != 'TK_COMMA'
			],
			body,
			token.text.strip('"\''),
			method_args_start.previous.text,  # type: ignore[attr]
		)
	return token.text.strip('"\'')
	# print(describe_token(try_block_start.parent.parent))


@dataclass
class InterestingThings:
	api_functions: Collection[APIFunction]
	"""All JavaScript functions that call an API endpoint that we were able to detect."""
	other_api_urls: Collection['URL']
	"""Other strings in the file that look like API endpoints, but were called in a different way."""
	static_urls: Collection['URLPath']
	"""References to static content, usually images or audio."""
	jsons: Mapping['FunctionID', 'JSONData']
	"""JSON data stored as literal argument to JSON.parse"""
	other_urls: Collection['URL']
	"""Other strings which are likely URLs"""


def _find_interesting_things_in_js(js: 'JSSource', path_for_log: Any | None = None):
	tokens = tokenize_js(js, {'unescape_strings': False})
	# unescape_strings is going to screw with the JSON parsing, because we need to make sure it doesn't end up having surrogates in it, otherwise I stay up until 2am wondering why errors are happening
	# TODO: Stuff starting with data:image/png etc maybe

	static_urls: set[URLPath] = set()
	api_urls: set[URL] = set()
	other_urls: set[URL] = set()
	api_functions: list[APIFunction] = []
	jsons: dict[FunctionID, JSONData] = {}
	for token in tokens:
		if token.type == 'TK_STRING':
			text: str = token.text.strip('"\'')
			if text.startswith(('/_next/static/', '_next/static')):
				static_urls.add(URLPath(text.removeprefix('/')))
			elif text.startswith(('/_next', '_next', 'https://', 'http://', 'ftp://')):
				other_urls.add(text.removeprefix('/'))

			if text.startswith('/api/'):
				api = parse_api_url(token, tokens)
				if isinstance(api, APIFunction):
					api_functions.append(api)
				else:
					api_urls.add(text)
			# TODO: Should we detect other hardcoded strings?
		if token.type == 'TK_WORD' and token.text == 'JSON':
			# TODO: Maybe we should have some list of remaining tokens to parse, and take the JSON.parse argument out of that list here, so we don't bother trying to see if it's an URL
			dot: Token = token.next  # type: ignore[assignment]
			func_name: Token = dot.next  # type: ignore[assignment]
			if func_name.text == 'parse':
				try:
					j = parse_json_literal(token, func_name, path_for_log)
				except UnexpectedParseError as e:
					logger.info('Unexpected parse error in %s: %s', path_for_log, e)
				except NotParseableError as e:
					logger.debug('%s was not parseable: %s', path_for_log, e)
				else:
					jsons[j[0]] = j[1]
	return InterestingThings(api_functions, api_urls, static_urls, jsons, other_urls)


async def find_interesting_things(
	js_path: Path, path_for_log: Any | None = None
) -> InterestingThings:
	"""path_for_log: If provided, this gets printed in the log instead of the whole entire js_path"""
	js = await read_text(js_path)
	return _find_interesting_things_in_js(js, path_for_log or js_path)
