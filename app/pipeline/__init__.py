"""The scheduled pipeline: what runs, in what order, and what happens when it breaks.

The harvesters under `research/` each do one job well and are run by hand. This
package is the layer above them — it decides which of them to call on a given
firing, isolates one dead source from the rest of the run, remembers across runs
which sources are failing, and records what each run actually did.

Nothing under `research/` is rewritten to make this work; `adapters.py` absorbs
the differences between their signatures.
"""
