"""Historical wire identifiers shared by the runtime artifact builders.

Module names can change independently of published filenames, JSON keys and
status values. Keep those serialized identifiers in the adjacent static resource.
This resource is required: missing or invalid data must fail visibly at import.
"""

import json
from pathlib import Path

CONTRACTS = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))
