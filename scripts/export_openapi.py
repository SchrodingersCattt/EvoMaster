import json
from pathlib import Path

from app import app


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    output_path = project_root / 'docs' / 'apifox-openapi.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    schema = app.openapi()
    output_path.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    print(output_path)


if __name__ == '__main__':
    main()
