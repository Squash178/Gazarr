import argparse
import sys

from sqlmodel import Session, select

from .database import engine, init_db
from .models import Magazine, Provider
from .schemas import MagazineCreate, ProviderCreate
from .services import create_magazine, create_provider


def parse_args(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed Gazarr with an initial provider and magazine.")
    parser.add_argument("--provider-name", required=True)
    parser.add_argument(
        "--provider-url", required=True, help="Torznab/Newznab endpoint (eg. https://prow.example/api)"
    )
    parser.add_argument("--provider-key", required=True, help="API key for the provider.")
    parser.add_argument("--magazine-title", required=True, help="Magazine title to add.")
    parser.add_argument("--magazine-regex", default=None, help="Optional custom search term.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv or sys.argv[1:])
    init_db()
    with Session(engine) as session:
        provider_exists = session.exec(select(Provider).where(Provider.name == args.provider_name)).first()
        if not provider_exists:
            create_provider(
                session,
                ProviderCreate(
                    name=args.provider_name,
                    base_url=args.provider_url,
                    api_key=args.provider_key,
                ),
            )
        magazine_exists = session.exec(select(Magazine).where(Magazine.title == args.magazine_title)).first()
        if not magazine_exists:
            create_magazine(
                session,
                MagazineCreate(title=args.magazine_title, regex=args.magazine_regex),
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

