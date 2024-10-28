#!/usr/bin/env python3

import asyncio
import logging
from argparse import ArgumentParser, BooleanOptionalAction
from pathlib import Path

from tqdm.contrib.logging import logging_redirect_tqdm

from .extractor import download_and_extract


def main() -> None:
	argparser = ArgumentParser(
		'geoguessr-source-extractor', 'Download and extract GeoGuessr website source'
	)
	argparser.add_argument(
		'website_source_dir', type=Path, help='Folder to save GeoGuessr source to'
	)
	argparser.add_argument(
		'extracted_data_dir', type=Path, help='Folder to save extracted files to'
	)
	argparser.add_argument(
		'--max_connections',
		type=int,
		help='Max connections to use when downloading, defaults to 1',
		default=1,
	)
	argparser.add_argument(
		'--force-redownload',
		action=BooleanOptionalAction,
		help='By default (--no-force-redownload), the source will not be redownloaded if it is already there, use this to make it redownload it anyway',
		default=False,
	)
	argparser.add_argument(
		'--download-static-files',
		action=BooleanOptionalAction,
		help='Try downloading images/audio/etc as well, defaults to true',
		default=True,
	)
	argparser.add_argument(
		'--force-redownload-static-files',
		help='By default (--no-force-redownload-static-files), static files will not be redownloaded if they are already there, use this to make it redownload it anyway',
		default=False,
	)
	levels = logging.getLevelNamesMapping()
	argparser.add_argument(
		'--log-level',
		help='Log level, by default logging.INFO',
		default='INFO',
		choices=levels.keys(),
	)

	args = argparser.parse_args()

	logging.basicConfig(level=levels.get(args.log_level, args.log_level))

	with logging_redirect_tqdm():
		asyncio.run(
			download_and_extract(
				args.website_source_dir,
				args.extracted_data_dir,
				max_connections=args.max_connections,
				force_redownload=args.force_redownload,
				download_static_files=args.download_static_files,
				force_redownload_static_files=args.force_redownload_static_files,
			)
		)


if __name__ == '__main__':
	main()
