"""
The RNAi target region, and a dsRNA design to be checked by someone who can.

RNA interference against a metabolic-resistance gene is a real and current line
in this species: published work reports dsRNA against a CYP6 P450 cutting
transcript levels sharply and restoring susceptibility to a pyrethroid. The
model's `cyp6` locus stands for that cluster, so handing over the sequence
region is the point at which this project touches nucleic-acid work at all.

**What this is not, and it matters.** There is not one nucleotide of analysis in
this project. No fold, no seed-region check, no BLAST against the assembly, no
off-target screen against the other P450s, esterases and GSTs in the same
genome. A GenBank record with a feature table looks like a designed construct,
and the word "construct" means something specific: a molecule whose specificity
has been checked. This one's has not. The disclaimer therefore goes in the FASTA
defline -- which survives being pasted into anything -- and in the GenBank
COMMENT, and the tests assert both are present.

The window is chosen by a stated rule, not by an analysis: the 5' third of the
coding sequence, where P450 paralogues diverge most and the conserved
heme-binding motif near the 3' end is furthest away. That is a heuristic for
reducing off-target risk. It is not a measurement of it.
"""
from __future__ import annotations

from .provenance import prose_block, stamp
from .spec import FormatSpec, FormatUnsupported, register
from . import sources

# Literature design criteria for insect dsRNA: a few hundred base pairs, since
# efficacy falls away sharply below about 19 bp of duplex and long dsRNA is
# processed into many siRNAs.
DEFAULT_LENGTH = 400
T7 = "TAATACGACTCACTATAGGG"          # T7 promoter, for in-vitro transcription

NOT_A_CONSTRUCT = (
    "NO specificity analysis was performed: no BLAST, no off-target screen "
    "against the other P450s, esterases or GSTs in this genome, no secondary "
    "structure prediction and no siRNA seed analysis. This is a region handed "
    "over for design, NOT a validated construct."
)


def _need_bio():
    try:
        from Bio.Seq import Seq                       # noqa: F401
        from Bio.SeqRecord import SeqRecord           # noqa: F401
    except ImportError as e:                          # pragma: no cover
        raise FormatUnsupported("rnai_target.fasta", "biopython") from e
    import Bio
    return Bio


def _record():
    """The fetched CYP6K1 record, parsed."""
    _need_bio()
    from Bio import SeqIO

    sources.require(
        "ncbi", "rnai_target.fasta",
        "The GenBank record for the RNAi target is not committed; data/README.md "
        "records the accession.",
        "Every other format is unaffected.")
    path = sources.RAW / f"{sources.NCBI_ACCESSION}.gb"
    return SeqIO.read(path, "genbank")


def target_window(start: int | None = None, length: int = DEFAULT_LENGTH) -> tuple[int, int]:
    """
    Where in the coding sequence to aim, one-based inclusive.

    The rule, stated so it can be argued with: begin one sixth of the way into
    the CDS, which puts the window in the 5' third where P450 paralogues are
    most divergent and well clear of the conserved heme-binding motif near the
    3' end. A heuristic, not an analysis.
    """
    cds_lo, cds_hi = sources.NCBI_CDS
    span = cds_hi - cds_lo + 1
    if length < 19:
        raise ValueError("below about 19 bp of duplex, RNAi efficacy falls away sharply")
    if length > span:
        raise ValueError(f"window of {length} bp does not fit in a {span} bp coding sequence")
    lo = cds_lo + span // 6 if start is None else int(start)
    if lo < cds_lo or lo + length - 1 > cds_hi:
        raise ValueError(f"window {lo}..{lo + length - 1} falls outside the CDS "
                         f"{cds_lo}..{cds_hi}")
    return lo, lo + length - 1


def _stats(seq: str) -> dict:
    gc = sum(seq.count(b) for b in "GCgc") / len(seq) if seq else 0.0
    return {"length_bp": len(seq), "gc_fraction": round(gc, 4)}


def rnai_target_fasta(*, start: int | None = None, length: int = DEFAULT_LENGTH,
                      generated: str | None = None) -> bytes:
    rec = _record()
    lo, hi = target_window(start, length)
    seq = str(rec.seq[lo - 1:hi])
    st = _stats(seq)
    # everything a reader needs is on the defline, because that is the one line
    # that survives being pasted into another tool
    defline = (
        f">blattella_cyp6_rnai_target|{sources.NCBI_ACCESSION}:{lo}-{hi}|"
        f"gene=CYP6K1|organism=Blattella_germanica|length={st['length_bp']}bp|"
        f"gc={st['gc_fraction']:.2f}|"
        f"selection=5prime_third_of_CDS_heuristic|{NOT_A_CONSTRUCT}"
    )
    body = "\n".join(seq[i:i + 60] for i in range(0, len(seq), 60))
    header = "".join(f"; {ln}\n" for ln in prose_block(stamp(
        "rnai_target.fasta",
        f"A {st['length_bp']} bp window of the CYP6K1 coding sequence "
        f"({sources.NCBI_ACCESSION}:{lo}-{hi}), offered as a starting point for "
        f"designing dsRNA against the metabolic-resistance locus the model calls cyp6.",
        NOT_A_CONSTRUCT + " The window was chosen by a stated heuristic, not by an "
        "analysis: the 5' third of the CDS, where P450 paralogues diverge most.",
        cli="python -m blattella.cli interop --format rnai_target.fasta",
        generated=generated,
    )).splitlines())
    return (header + defline + "\n" + body + "\n").encode()


def dsrna_construct_genbank(*, start: int | None = None, length: int = DEFAULT_LENGTH,
                            generated: str | None = None) -> bytes:
    """
    The target window with T7 promoters on both ends, annotated.

    Two opposing T7 promoters is the standard way to make double-stranded RNA by
    in-vitro transcription. Laying it out is a convenience for whoever takes this
    further; it is not evidence that the molecule works.
    """
    _need_bio()
    import io

    from Bio import SeqIO
    from Bio.Seq import Seq
    from Bio.SeqFeature import FeatureLocation, SeqFeature
    from Bio.SeqRecord import SeqRecord

    rec = _record()
    lo, hi = target_window(start, length)
    target = str(rec.seq[lo - 1:hi])
    rc_t7 = str(Seq(T7).reverse_complement())
    full = T7 + target + rc_t7

    out = SeqRecord(Seq(full), id="BgCYP6K1_dsRNA", name="BgCYP6K1_dsRNA",
                    description="Blattella germanica CYP6K1 dsRNA template, "
                                "UNVALIDATED design")
    out.annotations.update({
        "molecule_type": "DNA", "topology": "linear",
        "data_file_division": "SYN",
        "organism": "synthetic construct",
        "source": "synthetic construct",
        "comment": prose_block(stamp(
            "dsrna_construct.gb",
            f"A template for in-vitro transcription of dsRNA against CYP6K1: a "
            f"{length} bp window of {sources.NCBI_ACCESSION} ({lo}-{hi}) flanked by "
            f"opposing T7 promoters.",
            NOT_A_CONSTRUCT,
            cli="python -m blattella.cli interop --format dsrna_construct.gb",
            generated=generated,
            extra={"selection_rule": "5' third of the CDS, where P450 paralogues "
                                     "diverge most; a heuristic, not an analysis",
                   "before_you_order_this": "BLAST the target window against the "
                                            "Blattella germanica assembly and against "
                                            "the other detoxification gene families "
                                            "before synthesising anything."},
        )),
    })

    n = len(T7)
    for loc, kind, quals in [
        ((0, n), "promoter", {"note": ["T7 promoter, sense"], "standard_name": ["T7"]}),
        ((n, n + length), "misc_RNA",
         {"note": [f"CYP6K1 RNAi target window, {sources.NCBI_ACCESSION}:{lo}-{hi}",
                   NOT_A_CONSTRUCT],
          "gene": ["CYP6K1"], "organism": ["Blattella germanica"]}),
        ((n + length, len(full)), "promoter",
         {"note": ["T7 promoter, antisense; transcribing from both ends gives the "
                   "double-stranded product"], "standard_name": ["T7"]}),
    ]:
        f = SeqFeature(FeatureLocation(*loc), type=kind)
        f.qualifiers.update(quals)
        out.features.append(f)

    buf = io.StringIO()
    SeqIO.write(out, buf, "genbank")
    return buf.getvalue().encode()


register(FormatSpec(
    id="rnai_target.fasta", title="RNAi target region",
    spec="FASTA", spec_url="https://en.wikipedia.org/wiki/FASTA_format",
    media_type="text/x-fasta", filename="blattella_cyp6_rnai_target.fasta",
    method="GET", path="/api/interop/rnai_target.fasta",
    stage="nucleic acid design",
    consumers=("BLAST", "ViennaRNA", "Benchling", "SnapGene", "siDirect"),
    audience=("resistance-management",),
    what_it_is="A window of the CYP6K1 coding sequence, offered as a starting point "
               "for designing dsRNA against the metabolic-resistance locus.",
    what_it_is_not=NOT_A_CONSTRUCT + " The window follows a stated heuristic rather "
                                     "than an analysis.",
    cli="python -m blattella.cli interop --format rnai_target.fasta",
    render=rnai_target_fasta, requires=("ncbi",),
    caveats=("BLAST this against the genome before acting on it.",),
))

register(FormatSpec(
    id="dsrna_construct.gb", title="dsRNA template with T7 flanks",
    spec="GenBank flat file",
    spec_url="https://www.ncbi.nlm.nih.gov/genbank/samplerecord/",
    media_type="chemical/seq-na-genbank", filename="blattella_cyp6_dsrna.gb",
    method="GET", path="/api/interop/dsrna_construct.gb",
    stage="nucleic acid design",
    consumers=("Benchling", "SnapGene", "Geneious"),
    audience=("resistance-management",),
    what_it_is="The target window flanked by opposing T7 promoters, laid out for "
               "in-vitro transcription.",
    what_it_is_not="NOT a validated construct, and the layout is not evidence that "
                   "the molecule works. " + NOT_A_CONSTRUCT,
    cli="python -m blattella.cli interop --format dsrna_construct.gb",
    render=dsrna_construct_genbank, requires=("ncbi",),
    caveats=("Nothing in this project analyses nucleic acid. Screen for "
             "off-targets before synthesising anything.",),
))
