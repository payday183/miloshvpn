from pathlib import Path


# Папка, с которой начинать обход
ROOT_DIR = Path(".").resolve()

# Файл, куда будет сохранён результат
OUTPUT_FILE = ROOT_DIR / "project_code_dump.txt"


# Каталоги, которые нужно полностью пропускать
EXCLUDED_DIRS = {
    "env",
    ".env",
    "venv",
    ".venv",
    "__pycache__",

    ".git",
    ".github",
    ".idea",
    ".vscode",

    "node_modules",
    "dist",
    "build",
    "target",
    "out",

    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",

    "db",
    "database",
    "databases",
    "migrations",

    "logs",
    "tmp",
    "temp",
}


# Файлы, которые нужно пропускать
EXCLUDED_FILES = {
    OUTPUT_FILE.name,

    "README",
    "README.md",
    "readme.md",

    "LICENSE",
    "CHANGELOG.md",

    ".gitignore",
    ".dockerignore",

    ".env",
    ".env.example",
    ".env.local",

    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",

    "poetry.lock",
    "Pipfile.lock",
}


# Расширения файлов, которые точно не нужны
EXCLUDED_EXTENSIONS = {
    ".db",
    ".sqlite",
    ".sqlite3",

    ".md",
    ".txt",
    ".log",
    ".csv",
    ".tsv",

    ".jsonl",

    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",

    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",

    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",

    ".pyc",
    ".pyo",
    ".class",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
}


# Расширения файлов, которые считаем кодом
ALLOWED_EXTENSIONS = {
    ".py",

    ".js",
    ".jsx",
    ".ts",
    ".tsx",

    ".html",
    ".css",
    ".scss",
    ".sass",

    ".java",
    ".kt",

    ".c",
    ".cpp",
    ".h",
    ".hpp",

    ".cs",
    ".go",
    ".rs",

    ".php",
    ".rb",
    ".swift",

    ".sh",
    ".bat",
    ".ps1",

    ".sql",

    ".vue",
    ".svelte",

    ".xml",
}


def is_inside_excluded_dir(path: Path) -> bool:
    """
    Проверяет, находится ли файл внутри запрещённого каталога.
    """
    for part in path.parts:
        if part in EXCLUDED_DIRS:
            return True
    return False


def is_binary_file(path: Path) -> bool:
    """
    Проверяет, является ли файл бинарным.
    """
    try:
        with open(path, "rb") as file:
            chunk = file.read(1024)
            return b"\0" in chunk
    except Exception:
        return True


def read_text_file(path: Path) -> str:
    """
    Читает текстовый файл с несколькими возможными кодировками.
    """
    encodings = [
        "utf-8",
        "utf-8-sig",
        "cp1251",
        "latin-1",
    ]

    for encoding in encodings:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except Exception as error:
            return f"[Ошибка чтения файла: {error}]"

    return "[Не удалось прочитать файл: неизвестная кодировка]"


def should_skip_file(path: Path) -> bool:
    """
    Решает, нужно ли пропустить файл.
    """
    if path.name in EXCLUDED_FILES:
        return True

    if is_inside_excluded_dir(path):
        return True

    suffix = path.suffix.lower()

    if suffix in EXCLUDED_EXTENSIONS:
        return True

    if suffix not in ALLOWED_EXTENSIONS:
        return True

    if is_binary_file(path):
        return True

    return False


def main() -> None:
    files_count = 0

    with open(OUTPUT_FILE, "w", encoding="utf-8") as output:
        for path in ROOT_DIR.rglob("*"):
            if not path.is_file():
                continue

            if should_skip_file(path):
                continue

            relative_path = path.relative_to(ROOT_DIR)
            content = read_text_file(path)

            output.write(f"ФАЙЛ: {relative_path}\n")
            output.write("-" * 80)
            output.write("\n")
            output.write(content)
            output.write("\n\n")
            output.write("=" * 80)
            output.write("\n\n")

            files_count += 1

    print("Готово.")
    print(f"Файлов записано: {files_count}")
    print(f"Результат сохранён в: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()