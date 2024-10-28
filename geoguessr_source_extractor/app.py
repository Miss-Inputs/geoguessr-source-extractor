"""_next/static/chunks/pages/_app-*.js"""

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import pydantic

from .tokenize import tokenize_js

if TYPE_CHECKING:
	from jsbeautifier.core.token import Token

	from .typedefs import FunctionID, JSSource, ModuleID

_dict_list_adapter = pydantic.TypeAdapter(dict[str, list[int]])


def parse_localizations_from_app(
	app: 'JSSource',
) -> Mapping[tuple['ModuleID', 'FunctionID'], PurePosixPath]:
	"""Finds where localization strings are defined, from pages/_app-*.js"""
	# Function 96979: Translator function, in case you see that being called
	tokens = tokenize_js(app)
	# We gotta find function 15288, which has a big ass dict in it
	# Yeah I know this is getting VERY specific, not to mention very fragile
	function_15288 = next(
		t
		for t in tokens
		if t.previous
		and t.previous.type == 'TK_OPERATOR'
		and t.previous.text == ':'
		and t.previous.previous.text == '15288'
	)
	# "15288: function(e, t, n)"
	function_args_start: Token | None = function_15288.next
	assert function_args_start
	function_args_end: Token | None = function_args_start.closed
	assert function_args_end
	function_body_start: Token = function_args_end.next
	function_body = [t for t in tokens if t.parent == function_body_start]

	r_start = next(
		t
		for t in function_body
		if t.previous.type == 'TK_EQUALS'
		and t.previous.previous.text == 'r'
		and t.previous.previous.previous.text == 'var'
	)
	dict_source = ''
	token = r_start
	while token != r_start.closed:
		dict_source += token.text
		token = token.next
	dict_source += token.text

	r = _dict_list_adapter.validate_json(dict_source)
	# key: Relative URL fragment, something like ./en-US/country.json for example
	# list is two ints, the first one is often the same as the second one
	# I think it's [function ID, module ID]? But could be wrong
	return {(v[1], v[0]): PurePosixPath(k) for k, v in r.items()}
