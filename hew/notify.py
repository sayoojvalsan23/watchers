"""
Dispatch.

Alert text is NEVER generated at send time. Templates are fixed, pre-translated
and human-verified. An alert string is a life-safety artifact.

NOTE: V1 emits DISTANCE AND BAND, never an ETA. Propagation modelling is not
validated (design doc s10). A wrong ETA is worse than no ETA — people told they
have an hour who have twenty minutes are worse off than people told nothing.
"""

import logging
import re

log = logging.getLogger("hew.notify")

TEMPLATES = {
    "advisory": {
        "en": ("HAZARD ALERT: Possible upstream slope or ice failure detected "
               "near {site}. Downstream river reach {reach}. "
               "Places on this reach include {settlements}. "
               "Verify and stand by. Do not enter the river channel."),
    },
    "warning": {
        "en": ("URGENT: Upstream failure detected near {site}. "
               "A flood surge may travel down {reach}. "
               "Places on this reach include {settlements}. "
               "If you are anywhere along this river, move away from it to "
               "high ground NOW. Do not wait to see the water."),
    },
}

# How many settlements to name. An alert has to be readable at a glance on a
# phone by someone who is frightened; a list of ninety is not readable, and a
# truncated list that looks complete is dangerous.
NAME_LIMIT = 4


def _is_opaque(name):
    """
    Not every string is a place name. Two kinds fail here:
    inventory identifiers (HMA_GLI_761 -- the hazard registry is the NSIDC
    lake inventory, whose features are numbered, not named), and OSM
    placeholders ("unnamed channel"). Neither tells a resident anything.
    """
    if not name:
        return True
    n = name.strip().lower()
    # Inventory identifiers, from every registry source we merge. This guard
    # was written for HMA_GLI_ ids and silently missed RGI ones when glacier
    # outlines were added, putting "RGI2000-v7.0-G-15-05746" into a public
    # alert. Match the shape, not a fixed list of prefixes.
    if re.match(r"^[A-Za-z]{2,}[\d._-]*[-_]?v?[\d.]+[-_].*\d", name) or \
            name.startswith(("HMA_GLI_", "RGI")):
        return True
    return (name.replace("_", "").isdigit()
            or "unnamed" in n or n in ("?", "-", "none"))


class Dispatcher:
    """Console channel. Swap for FCM/SMS/webhook without touching callers."""
    channel = "console"

    def slots(self, result, corridor=None):
        """
        Structured facts for the fixed template. Nothing here is generated
        text -- these only choose WHICH known fact fills a named slot.

        Both slots prefer the routed corridor over registry fields, because
        routing knows real place and channel names and the lake inventory
        does not.
        """
        site = result.get("nearest_site")
        reach = result.get("reach_id")
        if corridor:
            if _is_opaque(site):
                # The topmost settlement anyone would recognise.
                named_place = next((c["name"] for c in corridor
                                    if not _is_opaque(c.get("name"))), None)
                site = f"the headwaters above {named_place}" if named_place else None
            if not reach:
                seq = []
                for c in corridor:
                    ch = c.get("channel")
                    if not _is_opaque(ch) and ch not in seq:
                        seq.append(ch)
                if seq:
                    # "or", not "then": under source-location uncertainty the
                    # branches are ALTERNATIVES, not a sequence. "then" would
                    # tell a resident the water goes down one and into the
                    # other, which is not what the corridor means.
                    reach = ("the " + " or the ".join(seq[:2])) if len(seq) > 1 \
                        else "the " + seq[0]
        if _is_opaque(site):
            site = "an upstream catchment"

        # Settlements are NAMED, not enumerated. The wording says "include"
        # and always states there are others, because a list that reads as
        # complete tells everyone not on it that they are safe -- and the
        # corridor is a lateral buffer, not a modelled inundation extent.
        named = "places all along this river"
        if corridor:
            picks = [c["name"] for c in corridor
                     if not _is_opaque(c.get("name"))][:NAME_LIMIT]
            rest = len(corridor) - len(picks)
            if picks:
                named = ", ".join(picks)
                named += f" and {rest} more places downstream" if rest > 0 \
                    else " and other places downstream"
        return {"site": site, "reach": reach or "the river below",
                "settlements": named}

    def render(self, tier, result, corridor=None):
        t = TEMPLATES.get(tier, {}).get("en")
        if not t:
            return None
        return t.format(**self.slots(result, corridor))

    def send(self, tier, feature, result, corridor=None):
        msg = self.render(tier, result, corridor)
        if not msg:
            return False, "no_template"
        log.warning("DISPATCH [%s] %s", tier.upper(), msg)
        return True, None
