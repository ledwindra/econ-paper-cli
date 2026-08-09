# Research-question extraction

## Goal

`extract_research_question` turns detected Abstract and Introduction sections
into a structured research question with traceable section provenance. It is an
application service over the replaceable `Generator` protocol and performs no
filesystem or network access itself.

## Current policy

`research-question-extraction-v2` is the default. The service follows this
sequence:

1. It keeps non-empty Abstract and Introduction sections in canonical order.
2. It builds a `GenerationRequest` asking for one research-question sentence
   and a citation to the section from which the question was drawn.
3. It validates the returned `GenerationResponse` against the supplied section
   evidence.
4. It derives `ResearchQuestionEvidence` from the first validated span of each
   cited section. The model does not supply page numbers, excerpts, or character
   offsets under v2.
5. It classifies the question as `explicit` when a cited section contains an
   interrogative sentence marker (`?`), and as `inferred` otherwise.

The older `research-question-extraction-v1` policy remains readable for stored
records and explicit callers. Under v1, the model returned nested JSON with its
own excerpt text and offsets. That contract is not the default because local
models could not report exact offsets reliably.

## Outcomes and warnings

An available result contains a non-empty question, one or more cited section
kinds, and derived evidence. An unavailable result contains no question or
evidence and carries at least one terminal warning.

The stable warning codes are:

- `no_usable_sections`: neither section was available, so generation was
  skipped;
- `missing_section`: only one of Abstract or Introduction was available;
- `generation_failed`: generation or generation-response validation failed;
- `model_abstained`: the generator reported insufficient evidence;
- `malformed_structured_response`: the returned question was empty or exceeded
  the v2 safety bound, or legacy v1 output did not match its JSON schema; and
- `ungrounded_evidence`: cited section identity could not be mapped to the
  supplied sections or exact provenance could not be derived.

## Grounding boundary

The service proves that each evidence record is derived from a section supplied
to the generator and that its page-local offsets match validated section spans.
It does not prove that the question sentence is entailed by the excerpt or that
the paper would describe the same sentence as its primary research question.

The application-layer transformation is deterministic given identical section
input and an identical generator response. Determinism of model generation is a
separate adapter responsibility.
