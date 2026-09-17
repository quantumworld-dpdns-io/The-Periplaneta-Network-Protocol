"""
Deployment strategies for insecticide resistance management.

The question the project is built to answer lives here: rotation, mixture, or
single-product use. The three differ in *when* and *how many* actives the colony
meets, and the comparison is only meaningful if they are matched on total
insecticide applied.

* **single**   -- one active, every generation. The baseline that fails.
* **rotation** -- one active per generation, cycling. An allele selected in one
  generation is unselected in the next two, and pays its fitness cost meanwhile.
* **mixture**  -- several actives at once, each at a reduced dose so the total
  load matches the other arms. Surviving requires resistance to all of them at
  once, which is rare; the counter-argument is that it selects for the one
  mechanism that covers everything, which here is metabolic.
* **untreated** -- the control. Resistance should decay.

Dose accounting: a mixture of *k* actives gives each a `1/k` share by default, so
every arm applies the same total quantity per generation. `dose_share=1.0`
switches to full dose of each, which is what a label-rate mixture actually is and
which is a different experiment; it is exposed rather than assumed.
"""
from __future__ import annotations

from dataclasses import dataclass

ACTIVE_SET = ("deltamethrin", "imidacloprid", "fipronil")


@dataclass(frozen=True)
class Strategy:
    """A named deployment schedule. Each entry is the set of actives for one generation."""

    name: str
    schedule: tuple[tuple[str, ...], ...]
    dose_share: float
    description: str

    def actives_for(self, generation: int) -> tuple[str, ...]:
        if not self.schedule:
            return ()
        return self.schedule[generation % len(self.schedule)]

    @property
    def period(self) -> int:
        return len(self.schedule)


def single(active: str = "deltamethrin") -> Strategy:
    return Strategy(
        name=f"single:{active}",
        schedule=((active,),),
        dose_share=1.0,
        description=f"{active} every generation",
    )


def rotation(actives: tuple[str, ...] = ACTIVE_SET, period: int = 1) -> Strategy:
    """One active at a time, each held for `period` generations before switching."""
    sched: list[tuple[str, ...]] = []
    for a in actives:
        sched.extend([(a,)] * period)
    return Strategy(
        name=f"rotation:{'-'.join(a[:4] for a in actives)}" + (f"/{period}" if period > 1 else ""),
        schedule=tuple(sched),
        dose_share=1.0,
        description=f"one active per {period} generation(s), cycling through {len(actives)}",
    )


def mixture(actives: tuple[str, ...] = ACTIVE_SET, dose_share: float | None = None) -> Strategy:
    """
    All actives together. By default each gets `1/k` of a dose so the total load
    matches the single and rotation arms.
    """
    share = (1.0 / len(actives)) if dose_share is None else dose_share
    return Strategy(
        name=f"mixture:{'-'.join(a[:4] for a in actives)}" + ("" if dose_share is None else f"@{share:g}"),
        schedule=(tuple(actives),),
        dose_share=share,
        description=f"{len(actives)} actives simultaneously at {share:.2f} dose each",
    )


def untreated() -> Strategy:
    return Strategy(name="untreated", schedule=((),), dose_share=0.0,
                    description="no insecticide; the control")


# The arms, keyed by the id every surface uses. Previously this mapping existed
# three times -- in standard_arms(), in the API's _strategy(), and in the request
# schema's Literal -- and the API paired ids to arms with zip() against a
# hard-coded tuple, so a sixth arm would have been dropped without an error.
ARMS: dict[str, callable] = {
    "single": lambda: single("deltamethrin"),
    "rotation": lambda: rotation(ACTIVE_SET, period=1),
    "rotation3": lambda: rotation(ACTIVE_SET, period=3),
    "mixture": lambda: mixture(ACTIVE_SET),
    "untreated": lambda: untreated(),
}

# Arms the headline comparison is defined over. Anything outside this set is
# opt-in: adding to it would regenerate the phase 6 answer, the committed
# comparison tables and every dashboard snapshot.
STANDARD = ("single", "rotation", "rotation3", "mixture", "untreated")


def arm(name: str) -> Strategy:
    if name not in ARMS:
        raise KeyError(f"unknown strategy {name!r}; have {sorted(ARMS)}")
    return ARMS[name]()


def standard_arms() -> tuple[Strategy, ...]:
    """The comparison the headline question asks for, matched on total dose."""
    return tuple(arm(k) for k in STANDARD)
