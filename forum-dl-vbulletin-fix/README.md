# forum-dl vBulletin `&lt;base&gt;` tag crash fix

[`forum-dl`](https://github.com/mikwielgus/forum-dl) (a generic
forum-to-mailbox archiver, similar in spirit to `gallery-dl`) fails to
detect vBulletin forums that don't emit a `<base href="...">` tag — an
optional, non-mandatory HTML element that plenty of real vBulletin
installs (especially older/customized ones) simply don't include.

## Symptom

```
forum_dl.exceptions.ExtractorNotFoundError: http://example.com/showthread.php?t=1234
```
...even against a confirmed, live, working vBulletin forum — no useful
error pointing at the real cause.

## Root cause

`VbulletinExtractor._detect()` (`forum_dl/extractors/vbulletin.py`)
correctly finds and checks the `<meta name="generator" content="vBulletin...">`
tag first — that part works. It then unconditionally does:

```python
base = soup.find("base")
return VbulletinExtractor(session, base.get("href"), options)
```

`Soup.find()` (this project's own wrapper, not BeautifulSoup's) *raises*
`TagSearchError` when nothing matches, rather than returning `None`. That
exception is a subclass of `SearchError`, which the generic
`Extractor.detect()` wrapper catches and silently converts into "this
extractor doesn't match" — so a page that correctly identified as
vBulletin via its generator tag gets rejected anyway, with the real
exception never surfacing to the user. Confirmed by instrumenting
`_detect()` directly: `generator_meta` resolves correctly
(`content="vBulletin 3.8.7"`), then `soup.find("base")` throws.

## Fix

Use the project's existing `soup.try_find()` (returns `None` instead of
raising) and fall back to deriving the base URL from the response's own
resolved URL when no `<base>` tag exists:

```python
base = soup.try_find("base")
base_url = base.get("href") if base else urljoin(response.url, "/")
return VbulletinExtractor(session, base_url, options)
```

See `base-tag-fallback.patch` for the exact diff (against
`mikwielgus/forum-dl` at the commit installed via `pip install -e .` /
`pipx install` at the time this was found — check line numbers still
match before applying to a newer checkout).

## Apply

```bash
cd forum-dl/   # your clone of the upstream repo
git apply /path/to/base-tag-fallback.patch
pipx install --global --force .   # or: pip install -e . in whatever env you use
```

## What this does NOT fix

`forum-dl`'s vBulletin extractor is written exclusively against
**vBulletin 5**'s HTML structure (`class="crumb-link"` breadcrumbs,
`class="b-post__title"` post markers, etc.). The much older **vBulletin
3.x** template family (still running on plenty of long-lived forums) uses
completely different markup with none of those classes, so
`_get_node_from_url()` silently falls through to returning the site's
root board for *any* thread URL on a vB3 site — detection now succeeds,
but thread/post extraction still doesn't work. That would need actual new
parsing logic for vB3's markup, not a patch like this one.
