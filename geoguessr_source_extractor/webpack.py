from collections.abc import Sequence
from typing import TYPE_CHECKING

from .tokenize import tokenize_js
from .typedefs import URLPath

if TYPE_CHECKING:
	from jsbeautifier.core.token import Token

	from .typedefs import JSSource, ModuleID


def _is_h_u_function(token: 'Token'):
	if token.type == 'TK_WORD' and token.text == 'h':
		next_token = token.next
		if next_token is None:
			# type hinters say they could never do it
			return False
		if next_token.text == '.':
			next_next_token = next_token.next
			if next_next_token is None:
				return False
			return next_next_token.text == 'u'
	return False


def parse_webpack(webpack_js: 'JSSource') -> Sequence[URLPath]:
	tokens = tokenize_js(webpack_js)
	# hrm now this gets weird
	# Right now, the function we want to look at is declared as h.u = function(e) {...}
	func_h: Token = next(t for t in tokens if _is_h_u_function(t))
	func_h_u: Token = func_h.next.next  # type: ignore[assignment,attr]
	func_h_u_eq: Token = func_h_u.next  # type: ignore[assignment]
	func_start: Token = func_h_u_eq.next  # type: ignore[assignment]
	func_args_start: Token = func_start.next  # type: ignore[assignment]
	func_args_end: Token | None = func_args_start.closed  # type: ignore[assignment]
	assert func_args_end, 'uh oh'
	func_body_start: Token = func_args_end.next  # type: ignore[assignment]
	func_body_end: Token = func_body_start.closed  # type: ignore[assignment]
	func_body = [t for t in tokens if t.parent == func_body_start]

	d: dict[ModuleID, URLPath] = {}
	# {Module ID: path to chunk)
	token = func_body[0]
	value_token: Token | None = None  # c, in a ? b : c
	while token != func_body_end:
		# hrm this will be annoying
		# To be fair it is also annoying for a human to try reading it
		# Lots of nested ternary cases
		if (
			token.type == 'TK_WORD'  # type: ignore[attr]
			and token.next.type == 'TK_OPERATOR'  # type: ignore[attr]
			and token.next.text == '==='  # type: ignore[attr]
			and token.next.next.type == 'TK_WORD'  # type: ignore[attr]
			and token.next.next.text == 'e'  # type: ignore[attr]
		):
			key: str = token.text  # type: ignore[attr]
			question_mark: Token = token.next.next.next  # type: ignore[attr]
			value_token = question_mark.next
			if value_token.type == 'TK_STRING':  # type: ignore[attr]
				value: str = value_token.text.strip('"\'')  # type: ignore[attr]
				while not (value_token.next.type == 'TK_OPERATOR' and value_token.next.text == ':'):  # type: ignore[attr]
					value_token = value_token.next  # type: ignore[attr]
					if value_token.type == 'TK_OPERATOR' and value_token.text == '+':  # type: ignore[attr]
						continue
					value += (
						key
						if value_token.type == 'TK_WORD' and value_token.text == 'e'  # type: ignore[attr]
						else value_token.text.strip('"\'')  # type: ignore[attr]
					)
				d[int(key)] = URLPath('_next/') / value
				token = value_token.next  # type: ignore[attr]
		token = token.next  # type: ignore[attr]
	assert value_token
	last_ternary_case: Token = value_token.next.next  # type: ignore[attr]
	token = last_ternary_case
	# We already know it's "static/chunks/" + e + "." + ({big ass dict})[e]
	while token != func_body_start.closed:
		if token.type == 'TK_START_EXPR' and token.text == '(':  # type: ignore[attr]
			big_dict_start = token.next  # type: ignore[attr]
			# int keys, so we can't just parse it as JSON, going to have to do it manually
			while token != big_dict_start.closed:  # type: ignore[attr]
				token = token.next  # type: ignore[attr]
				if token.next.type == 'TK_OPERATOR' and token.next.text == ':':  # type: ignore[attr]
					key = token.text  # type: ignore[attr]
					value_token = token.next.next  # type: ignore[attr]
					value = value_token.text.strip('"\'')  # type: ignore[attr]
					d[int(key)] = URLPath(f'_next/static/chunks/{key}.{value}.js')
					token = value_token.next  # Comma #type: ignore[attr]
			# big_dict = ''
			# while token != big_dict_start.closed:
			# 	token = token.next
			# 	big_dict += token.text
			# d.update({k: f'static/chunks/{k}.{v}.js' for k, v in json.loads(big_dict).items()})
			break
		token = token.next  # type: ignore[attr]

	return tuple(d.values())
