"""Fixed knowledge-index values shared by settings, storage, and retrieval."""

EMBEDDING_DIMENSIONS = 128
ALLOWED_EXTENSIONS = frozenset({".pdf", ".docx", ".txt", ".md"})
CATEGORIES = frozenset({"Knowledge", "Policy"})
# Folder name under knowledge-intake, and the category stored for its files.
INTAKE_FOLDERS = {
    "ora-19c-manual": "Knowledge",
    "Policy": "Policy",
}
LOCAL_DISCLOSURE = (
    "Embeddings were generated locally. Document text was not sent to a remote service."
)
DEFAULT_ROLES = ("operator", "dba", "admin")
PHASE7_ROLES = frozenset({"viewer", "operator", "dba", "platform_admin", "security_auditor"})
ALLOWED_ROLES = frozenset({*DEFAULT_ROLES, "auditor", *PHASE7_ROLES})
ADMIN_OPERATOR_ROLE = "admin"
NON_ADMIN_OPERATOR_ROLES = frozenset({"operator", "dba", "auditor", "viewer", "security_auditor"})
PRIORITIZATION = (
    "Policy passages are labeled authoritative and do not receive a hidden rank boost. "
    "Filter category to Policy or Knowledge when one collection should be searched alone."
)
