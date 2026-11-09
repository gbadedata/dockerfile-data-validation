# CI evidence

Excerpts from GitHub Actions job logs, stored here because Actions logs on a
public repository expire (90 days by default). Timestamps and colour codes are
stripped; otherwise the text is as CI printed it.

| File | Run | What it shows |
|---|---|---|
| [trivy-first-run-zlib.txt](trivy-first-run-zlib.txt) | [42592962916](https://github.com/gbadedata/dockerfile-data-validation/actions/runs/42592962916) | The first CI run. The image built and passed every other gate, then Trivy failed the build: `zlib1g` in `python:3.12-slim` (Debian 13.7) had two HIGH CVEs with a fixed Debian package available. |
| [trivy-green-run.txt](trivy-green-run.txt) | [42597537309](https://github.com/gbadedata/dockerfile-data-validation/actions/runs/42597537309) | After the runtime stage started applying Debian security updates (commit `999c472`): no fixable HIGH or CRITICAL findings in the Debian or Python packages. |
| [verify-image.txt](verify-image.txt) | 42597537309 | All 22 labels pass the standard and match the manifest inside the image; the selfcheck re-hashes the 10 shipped files; the image runs as UID 10001. |
| [image-labels.json](image-labels.json) | 42597537309 | The 22 labels, rebuilt from the `check-labels` lines in `verify-image.txt`, which print each value in full. |
| [rejections.txt](rejections.txt) | 42597537309 | The 7 broken inputs, each rejected with the expected message. |
| [publish.txt](publish.txt) | 42597537309 | One digest pushed to GHCR under three tags, pulled back from the registry and verified again. |

## Notes

- **Run 42592962916 ran on commit `e06c26c`, which is no longer in the
  history.** The history was rewritten afterwards to edit commit messages only.
  The file contents are unchanged: `e06c26c` and `854351d` have the same tree.
- **Trivy's title for CVE-2026-76844 names an unrelated package**
  (webpack-dev-middleware). The affected package and the fixed version in the
  same row are `zlib1g`, so this looks like a labelling error in the
  vulnerability database rather than a second finding.
- Run 42597537309 built commit `23a2f83`, so the `revision` and `created` labels
  in these files belong to that build. Later builds carry their own values.
