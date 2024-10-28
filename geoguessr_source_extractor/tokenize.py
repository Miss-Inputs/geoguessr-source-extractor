"""Putting everything else related to jsbeautifier here"""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import jsbeautifier
from jsbeautifier.javascript.tokenizer import Tokenizer

if TYPE_CHECKING:
	from jsbeautifier.core.token import Token


def tokenize_js(js: str, options: Mapping[str, Any] | None=None) -> 'Sequence[Token]':
	beautifier_options = jsbeautifier.BeautifierOptions(options)
	tokenizer = Tokenizer(js, beautifier_options)
	token_stream = tokenizer.tokenize()
	return tuple(token_stream)

def describe_token(token: 'Token') -> dict[str, str | None | tuple[str | None, str | None] | Any]:
	return {
		'type': token.type,
		'text': token.text,
		'opened': (token.opened.type, token.opened.text) if token.opened else None,
		'closed': (token.closed.type, token.closed.text) if token.closed else None,
		'directives': token.directives,
		'parent': (token.parent.type, token.parent.text) if token.parent else None,
		'previous': (token.previous.type, token.previous.text) if token.previous else None,
		'next': (token.next.type, token.next.text) if token.next else None,
		'newlines': token.newlines,
		'comments_before': token.comments_before,
		'whitespace_before': token.whitespace_before,
	}
