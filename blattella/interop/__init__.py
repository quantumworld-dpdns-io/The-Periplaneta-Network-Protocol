"""
Interoperability: the project's results in formats other software reads.

Importing this package registers every available format in `spec.FORMATS`. The
API, the CLI and the dashboard all enumerate that registry rather than keeping
their own lists, so a format cannot exist on one surface and not another.
"""
from __future__ import annotations

from .spec import (  # noqa: F401
    Artifact, Availability, DataUnavailable, FORMATS, FormatSpec, FormatUnsupported,
    availability, discovery, render,
)

# side-effect imports: each module registers the formats it can write
from . import sources  # noqa: F401,E402
from . import tables  # noqa: F401,E402
from . import sbml  # noqa: F401,E402
from . import ligands  # noqa: F401,E402
from . import fep  # noqa: F401,E402
from . import sequence  # noqa: F401,E402
from . import omex  # noqa: F401,E402

__all__ = ["Artifact", "Availability", "DataUnavailable", "FORMATS", "FormatSpec",
           "FormatUnsupported", "availability", "discovery", "render"]
