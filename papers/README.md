# papers/ (local only)

Put the technical papers you want the narrator to draw background from in this
folder: PDF, `.txt` or `.md`. Everything here except this README is git-ignored, so
the papers stay on the device and never enter the repository.

```
papers/
  README.md          <- tracked
  sel-papers.txt     <- tracked: the URLs, not the papers
  some-paper.pdf     <- yours, not tracked
  .rea_index.json    <- BM25 index, rebuilt automatically when files change
```

Good sources are SEL's free technical papers on selinc.com. `sel-papers.txt` lists six
on event report analysis, negative-sequence directional elements and inverse-time
elements, and `scripts/fetch_papers.sh` downloads them (once, while online) and builds the
index. Add your own URLs to that list or to another file, and follow the publisher's terms.

Then build the index (optional; `rea analyze` rebuilds it when files change):

```
PYTHONPATH=python python3 -m rea index-papers
```

How retrieval is used: the three best-matching passages are shown to the model as
BACKGROUND, for concepts only. The validator still rejects any number that the
physics core did not produce, so a number copied from a paper cannot reach the
report. The report lists which file and page each passage came from.

If the folder is empty, retrieval is simply skipped.
