# AI assistance log

Fill this in before you submit. An AI coding assistant drafted parts of this prototype. You still own the code, the design choices, and what you turn in. Replace the notes below with your own words after you have read the code and run it.

## What to record

- What you asked for
- What you kept
- What you changed or threw out
- How you checked it (tests you ran, screens you clicked, sample files you read)
- What is still unfinished

## Draft notes to replace

- The Assignment 2 design was the source of the pipeline shape: document store, candidate extraction, a separate quality gate, staff review, and a report that reads approved facts only.
- The default extractor is rule-based so the project runs with no key. A remote model is optional and off unless `LLM_PROVIDER=remote`.
- Sample documents are fictional. Do not swap in a real file.
- You still need to review the modules, run `pytest`, walk the Nguyen and Hale paths yourself, and write this log in your own words.

## Kept, changed, rejected

| Item | Your decision | Why |
| --- | --- | --- |
| Document Intelligence with a separate gate | | |
| Local line-rule extractor as the default | | |
| Conflicts block Ready; missing items do not | | |
| Anything you removed or rewrote | | |

## How you checked it

| Check | Result | Date |
| --- | --- | --- |
| `pytest` | | |
| Hale path in the browser or on the command line | | |
| Nguyen conflict blocks Ready | | |
| Ortiz exceptions produce no invented facts | | |
| Signed out, and `sam.patel`, cannot read a sample matter | | |
