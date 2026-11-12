"""Fixed knowledge-index values shared by settings, storage, and retrieval."""

EMBEDDING_DIMENSIONS = 128
ALLOWED_EXTENSIONS = frozenset({".pdf", ".docx", ".txt", ".md"})
CATEGORIES = frozenset({"Knowledge", "Policy"})
LOCAL_DISCLOSURE = (
    "Embeddings were generated locally. Document text was not sent to a remote service."
)
DEFAULT_ROLES = ("operator", "dba", "admin")
ALLOWED_ROLES = frozenset({*DEFAULT_ROLES, "auditor"})
ADMIN_OPERATOR_ROLE = "admin"
NON_ADMIN_OPERATOR_ROLES = frozenset({"operator", "dba", "auditor"})
PRIORITIZATION = (
    "Policy passages are labeled authoritative and do not receive a hidden rank boost. "
    "Filter category to Policy or Knowledge when one collection should be searched alone."
)
