import argparse
from pathlib import Path
from modules.local_data import import_manifest


def main():
    parser = argparse.ArgumentParser(
        description="Validate authorized LOCAL sample manifest. No network requests."
    )
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    samples = import_manifest(args.manifest)
    print(
        f"Validated {len(samples)} local samples. Independent human scores retained. No training performed."
    )


if __name__ == "__main__":
    main()
