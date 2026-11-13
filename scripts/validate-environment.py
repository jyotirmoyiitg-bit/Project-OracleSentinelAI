"""Validate the Oracle Sentinel AI environment."""

import os
import sys
from pathlib import Path


def check_python_version() -> bool:
    """Check Python version."""
    version = sys.version_info
    print(f"Python version: {version.major}.{version.minor}.{version.micro}")
    if version.major < 3 or (version.major == 3 and version.minor < 14):
        print("WARNING: Python 3.14+ is recommended")
        return False
    return True


def check_env_file() -> bool:
    """Check .env file exists."""
    env_path = Path(".env")
    if env_path.exists():
        print(f".env file found: {env_path}")
        return True
    else:
        print("WARNING: .env file not found. Copy .env.example to .env")
        return False


def check_tns_admin() -> bool:
    """Check TNS_ADMIN is set."""
    tns_admin = os.environ.get("TNS_ADMIN")
    if tns_admin:
        tns_path = Path(tns_admin)
        if tns_path.exists():
            print(f"TNS_ADMIN: {tns_admin} (exists)")
            return True
        else:
            print(f"WARNING: TNS_ADMIN path does not exist: {tns_admin}")
            return False
    else:
        print("INFO: TNS_ADMIN not set (optional for thin mode)")
        return True


def check_database_url() -> bool:
    """Check DATABASE_URL is set."""
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        print(f"DATABASE_URL: configured")
        return True
    else:
        print("WARNING: DATABASE_URL not set")
        return False


def check_directories() -> bool:
    """Check required directories exist."""
    dirs = [
        "backend",
        "backend/src/sentinel",
        "knowledge-intake",
        "knowledge-intake/ora-19c-manual",
        "knowledge-intake/Policy",
        "knowledge-intake/Quarantine",
        "scripts/oracle",
        "docs",
    ]
    all_ok = True
    for d in dirs:
        path = Path(d)
        if path.exists():
            print(f"Directory OK: {d}")
        else:
            print(f"WARNING: Missing directory: {d}")
            all_ok = False
    return all_ok


def main():
    """Run all environment checks."""
    print("=" * 60)
    print("Oracle Sentinel AI — Environment Validation")
    print("=" * 60)

    checks = [
        ("Python version", check_python_version),
        (".env file", check_env_file),
        ("TNS_ADMIN", check_tns_admin),
        ("DATABASE_URL", check_database_url),
        ("Directories", check_directories),
    ]

    results = []
    for name, check in checks:
        print(f"\n[{name}]")
        result = check()
        results.append((name, result))

    print("\n" + "=" * 60)
    print("Summary:")
    for name, result in results:
        status = "PASS" if result else "FAIL/WARN"
        print(f"  {status}: {name}")

    all_passed = all(r for _, r in results)
    if all_passed:
        print("\nEnvironment validation passed!")
    else:
        print("\nEnvironment validation completed with warnings.")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
