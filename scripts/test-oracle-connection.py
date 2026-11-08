#!/usr/bin/env python3
"""Standalone script to test Oracle GSYSTEM connectivity.

This script reads the .env configuration, decrypts the password if needed,
connects to the specified Oracle database using the GSYSTEM user, and reports
the connection status, version, and instance details.

Usage:
    # Auto-discover TNS aliases from tnsnames.ora and test the first one
    python scripts/test-oracle-connection.py

    # Test a specific TNS alias
    python scripts/test-oracle-connection.py --alias MYDB

    # List all discovered TNS aliases without connecting
    python scripts/test-oracle-connection.py --list-aliases

    # Test all discovered aliases
    python scripts/test-oracle-connection.py --all

    # Test with explicit credentials (overrides .env)
    python scripts/test-oracle-connection.py --alias MYDB --user GSYSTEM --password MyP@ss

Requirements:
    - python-oracledb (thin mode, no Oracle Client needed)
    - python-dotenv
    - cryptography (for Fernet decryption, if password is encrypted)

Exit codes:
    0 — Connection successful
    1 — Connection failed
"""

import argparse
import os
import re
import sys
from pathlib import Path

import oracledb
from dotenv import load_dotenv


def parse_tnsnames(tns_path: Path) -> list[str]:
    """Parse tnsnames.ora and return a list of TNS aliases.

    Handles standard Oracle tnsnames.ora format:
    - Aliases are lines that start at column 0 and end with ' ='
    - Comments start with '#'
    - Multi-line entries between (DESCRIPTION= ... )
    """
    aliases = []
    if not tns_path.exists():
        return aliases

    content = tns_path.read_text(encoding="utf-8", errors="replace")
    # Match alias names: lines starting with a word followed by ' ='
    # e.g., "MYDB =" or "PROD_DB ="
    pattern = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*=", re.MULTILINE)

    for match in pattern.finditer(content):
        alias = match.group(1).strip()
        # Skip comment lines
        line_start = content.rfind("\n", 0, match.start()) + 1
        line_prefix = content[line_start:match.start()].strip()
        if not line_prefix.startswith("#"):
            aliases.append(alias)

    return aliases


def decrypt_password(password: str, secret_key: str) -> str:
    """Decrypt a Fernet-encrypted password.

    If the password doesn't start with the Fernet prefix (gAAAAA),
    it's returned as-is (plain-text fallback).
    """
    FERNET_PREFIX = "gAAAAA"

    if not password.startswith(FERNET_PREFIX):
        return password

    try:
        from cryptography.fernet import Fernet

        fernet = Fernet(secret_key.encode("ascii"))
        decrypted = fernet.decrypt(password.encode("ascii")).decode("utf-8")
        return decrypted
    except Exception as e:
        print(f"ERROR: Failed to decrypt password: {e}", file=sys.stderr)
        print("Make sure SECRET_KEY in .env matches the key used to encrypt ORACLE_PASSWORD.", file=sys.stderr)
        sys.exit(1)


def load_config(env_path: str | None = None) -> dict:
    """Load configuration from .env file."""
    # Determine project root (parent of scripts/)
    project_root = Path(__file__).resolve().parent.parent

    if env_path is None:
        env_path = project_root / ".env"

    if not Path(env_path).exists():
        print(f"ERROR: .env file not found at {env_path}", file=sys.stderr)
        print("Copy .env.example to .env and fill in your values.", file=sys.stderr)
        sys.exit(1)

    load_dotenv(env_path)

    config = {
        "tns_admin": os.environ.get("TNS_ADMIN", "./tns"),
        "username": os.environ.get("ORACLE_USERNAME", "GSYSTEM"),
        "password": os.environ.get("ORACLE_PASSWORD", ""),
        "secret_key": os.environ.get("SECRET_KEY", ""),
        "alias": os.environ.get("ORACLE_TNS_ALIAS", ""),
    }

    # Resolve the TNS_ADMIN path relative to project root
    tns_path = Path(config["tns_admin"])
    if not tns_path.is_absolute():
        tns_path = project_root / tns_path
    config["tns_admin"] = str(tns_path)

    # Auto-discover TNS aliases from tnsnames.ora
    tnsnames_file = tns_path / "tnsnames.ora"
    aliases = parse_tnsnames(tnsnames_file)
    config["discovered_aliases"] = aliases

    # If no explicit alias set, use the first discovered one
    if not config["alias"] and aliases:
        config["alias"] = aliases[0]

    return config


def test_connection(alias: str, username: str, password: str, tns_admin: str) -> dict:
    """Test Oracle connection and return diagnostic info."""
    results = {
        "status": "error",
        "message": "",
        "version": None,
        "banner": None,
        "instance_name": None,
        "instance_status": None,
        "database_name": None,
        "pdb": None,
        "capabilities": {},
    }

    try:
        # Initialize Oracle client with custom TNS config
        oracledb.init_oracle_client(config_dir=tns_admin)

        # Connect
        conn = oracledb.connect(
            user=username,
            password=password,
            dsn=alias,
            config_dir=tns_admin,
        )

        cursor = conn.cursor()

        # Version banner
        cursor.execute("SELECT banner FROM v$version WHERE rownum = 1")
        row = cursor.fetchone()
        if row:
            results["banner"] = row[0].strip()
            results["version"] = row[0].strip()

        # Instance info
        cursor.execute("SELECT instance_name, status FROM v$instance")
        row = cursor.fetchone()
        if row:
            results["instance_name"] = row[0]
            results["instance_status"] = row[1]

        # Database name
        cursor.execute("SELECT name FROM v$database")
        row = cursor.fetchone()
        if row:
            results["database_name"] = row[0]

        # PDB context
        cursor.execute("SELECT sys_context('USERENV', 'CON_NAME') FROM dual")
        row = cursor.fetchone()
        if row:
            results["pdb"] = row[0]

        # Capability checks
        capabilities = {}

        # Check V$DIAG_ALERT_EXT availability
        try:
            cursor.execute("SELECT COUNT(*) FROM v$diag_alert_ext WHERE rownum = 1")
            cursor.fetchone()
            capabilities["v_diag_alert_ext"] = True
        except oracledb.Error:
            capabilities["v_diag_alert_ext"] = False

        # Check CDB/PDB mode
        try:
            cursor.execute("SELECT value FROM v$parameter WHERE name = 'compatible'")
            row = cursor.fetchone()
            if row:
                capabilities["compatible_version"] = row[0]
        except oracledb.Error:
            pass

        # Check tablespace views
        try:
            cursor.execute("SELECT COUNT(*) FROM dba_tablespaces")
            row = cursor.fetchone()
            capabilities["dba_tablespaces"] = row[0] if row else 0
        except oracledb.Error:
            capabilities["dba_tablespaces"] = "access denied"

        # Check FRA
        try:
            cursor.execute("SELECT recovery_area_used, recovery_area_target FROM v$recovery_file_dest")
            row = cursor.fetchone()
            if row:
                capabilities["fra_configured"] = True
                capabilities["fra_used_gb"] = round(row[0] / (1024**3), 2) if row[0] else 0
                capabilities["fra_target_gb"] = round(row[1] / (1024**3), 2) if row[1] else 0
            else:
                capabilities["fra_configured"] = False
        except oracledb.Error:
            capabilities["fra_configured"] = False

        # Check ARCHIVELOG mode
        try:
            cursor.execute("SELECT log_mode FROM v$database")
            row = cursor.fetchone()
            if row:
                capabilities["log_mode"] = row[0]
        except oracledb.Error:
            pass

        results["capabilities"] = capabilities
        results["status"] = "connected"
        results["message"] = "Connection successful"

        cursor.close()
        conn.close()

    except oracledb.Error as e:
        error_code = e.args[0].code if hasattr(e.args[0], "code") else "unknown"
        results["message"] = f"Oracle error {error_code}: {e.args[0].message if hasattr(e.args[0], 'message') else str(e)}"
    except FileNotFoundError as e:
        results["message"] = f"TNS config not found: {e}. Check TNS_ADMIN path in .env."
    except Exception as e:
        results["message"] = f"Unexpected error: {type(e).__name__}: {e}"

    return results


def print_results(results: dict) -> None:
    """Print formatted connection test results."""
    status = results["status"]
    emoji = "✅" if status == "connected" else "❌"

    print(f"\n{emoji} Connection Test Results")
    print("=" * 60)
    print(f"  Status:    {status.upper()}")
    print(f"  Message:   {results['message']}")

    if status == "connected":
        print(f"\n  Database Info:")
        if results["banner"]:
            print(f"    Banner:        {results['banner']}")
        if results["instance_name"]:
            print(f"    Instance:      {results['instance_name']}")
        if results["instance_status"]:
            print(f"    Status:        {results['instance_status']}")
        if results["database_name"]:
            print(f"    Database:      {results['database_name']}")
        if results["pdb"]:
            print(f"    PDB/CDB:       {results['pdb']}")

        caps = results.get("capabilities", {})
        if caps:
            print(f"\n  Capabilities:")
            for key, value in caps.items():
                print(f"    {key}: {value}")

    print("=" * 60 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test Oracle GSYSTEM connectivity using .env configuration"
    )
    parser.add_argument(
        "--alias",
        help="TNS alias to test (overrides .env ORACLE_TNS_ALIAS)",
    )
    parser.add_argument(
        "--user",
        help="Oracle username (overrides .env ORACLE_USERNAME)",
    )
    parser.add_argument(
        "--password",
        help="Oracle password (overrides .env ORACLE_PASSWORD)",
    )
    parser.add_argument(
        "--env",
        help="Path to .env file (default: project root/.env)",
    )
    parser.add_argument(
        "--tns-admin",
        help="Path to TNS config directory (overrides .env TNS_ADMIN)",
    )
    parser.add_argument(
        "--list-aliases",
        action="store_true",
        help="List all TNS aliases found in tnsnames.ora and exit",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="test_all",
        help="Test connection to all discovered TNS aliases",
    )

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.env)

    # Apply command-line overrides
    tns_admin = args.tns_admin or config["tns_admin"]
    username = args.user or config["username"]
    password = args.password or config["password"]

    # Discover TNS aliases from tnsnames.ora
    tns_path = Path(tns_admin)
    tnsnames_file = tns_path / "tnsnames.ora"
    discovered_aliases = parse_tnsnames(tnsnames_file)

    # --list-aliases mode: just show what's available
    if args.list_aliases:
        if discovered_aliases:
            print(f"Discovered TNS aliases from {tnsnames_file}:")
            for a in discovered_aliases:
                print(f"  - {a}")
        else:
            print(f"No TNS aliases found in {tnsnames_file}")
        sys.exit(0)

    if not discovered_aliases and not args.alias and not config["alias"]:
        print(f"ERROR: No TNS aliases found in {tnsnames_file}", file=sys.stderr)
        print("Add a TNS entry to tnsnames.ora or pass --alias <name>.", file=sys.stderr)
        sys.exit(1)

    # Determine which aliases to test
    if args.test_all:
        aliases_to_test = discovered_aliases if discovered_aliases else [args.alias or config["alias"]]
    else:
        # Priority: --alias flag > .env ORACLE_TNS_ALIAS > first discovered alias
        alias = args.alias or config["alias"]
        if not alias and discovered_aliases:
            alias = discovered_aliases[0]

        if not alias:
            print("ERROR: No TNS alias available.", file=sys.stderr)
            print("Add entries to tnsnames.ora, set ORACLE_TNS_ALIAS in .env, or pass --alias <name>.", file=sys.stderr)
            sys.exit(1)

        aliases_to_test = [alias]

    # Decrypt password if needed
    if not args.password and config["secret_key"]:
        password = decrypt_password(password, config["secret_key"])

    if not password:
        print("ERROR: No password provided.", file=sys.stderr)
        print("Set ORACLE_PASSWORD in .env or pass --password.", file=sys.stderr)
        sys.exit(1)

    # Validate TNS_ADMIN
    if not Path(tns_admin).exists():
        print(f"WARNING: TNS_ADMIN directory not found: {tns_admin}", file=sys.stderr)
        print("Make sure tnsnames.ora is in this directory.", file=sys.stderr)

    # Show discovered aliases
    if discovered_aliases and len(discovered_aliases) > 1:
        print(f"Discovered {len(discovered_aliases)} TNS alias(es) from {tnsnames_file}:")
        for a in discovered_aliases:
            marker = " <-- testing" if a in aliases_to_test else ""
            print(f"  - {a}{marker}")
        print()

    # Run test(s)
    all_results = []
    for alias in aliases_to_test:
        print(f"Testing connection to: {alias}")
        print(f"  User:      {username}")
        print(f"  TNS Admin: {tns_admin}")
        results = test_connection(alias, username, password, tns_admin)
        print_results(results)
        all_results.append((alias, results))

    # Exit code: 0 if all connected, 1 if any failed
    any_connected = any(r["status"] == "connected" for _, r in all_results)
    all_connected = all(r["status"] == "connected" for _, r in all_results)
    if len(aliases_to_test) > 1:
        connected_count = sum(1 for _, r in all_results if r["status"] == "connected")
        print(f"Summary: {connected_count}/{len(aliases_to_test)} connection(s) successful")
    sys.exit(0 if all_connected else 1)


if __name__ == "__main__":
    main()
