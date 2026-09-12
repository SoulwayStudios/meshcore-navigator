# Contributing to MeshCore Navigator

Small, focused contributions are welcome: documentation corrections, reproducible bug reports, and fixes with clear validation.

## What is a pull request?

A pull request (PR) proposes changes from one branch to another. It lets the maintainer inspect the diff, discuss the change, request revisions, and decide whether to merge it. Opening a PR does not change the project's default branch.

If you do not have write access to this repository, work in a **fork**: a copy under your own GitHub account. Your PR can propose changes from that fork back to this project.

## Open your first PR

1. Use GitHub's **Fork** button to create a copy of this repository under your account.
2. Clone your fork and create a branch. Replace `YOUR-USERNAME` with your GitHub username:

   ```bash
   git clone https://github.com/YOUR-USERNAME/meshcore-navigator.git
   cd meshcore-navigator
   git switch -c docs/clarify-readme
   ```

3. Make one focused change. Review it with `git diff`, and check any links or commands you changed. For application changes, also describe the tests you ran and any hardware or operating systems you could not check.
4. Follow the repository's [versioning rules](AGENTS.md): every new commit published to GitHub needs a patch-version increment; new features need a minor-version increment. The helper updates both version declarations:

   ```bash
   python scripts/bump_version.py
   ```

   Use `python3` if that is your Python command. For a new feature, add `--feature` instead. Preserve the early-development notice and Coffee link in the README and app About/Splash tabs.

5. Stage only the files you intend to contribute, inspect the staged diff, commit, and push. For a README-only change plus the required version bump:

   ```bash
   git add README.md meshcore_tray/__init__.py pyproject.toml
   git diff --cached
   git commit -m "Clarify README instructions"
   git push -u origin docs/clarify-readme
   ```

   Never include local configuration, credentials, private messages, databases, or logs in a contribution.

6. On GitHub, choose **Compare & pull request**. Check these destinations before submitting:

   - **Base repository:** `SoulwayStudios/meshcore-navigator`
   - **Base branch:** `main`
   - **Head repository:** your fork
   - **Compare branch:** your contribution branch

   Use a title describing the change. In the description, explain the problem, what changed, and how you checked it. Choose a **draft PR** if it is still in progress; otherwise, request review with a regular PR.

## Review and merge

The maintainer can review individual lines under **Files changed** and discuss the proposal in the PR conversation. Automated checks may run; a passing check only covers what that check actually tests.

To address feedback, make the revisions on the same branch, apply the versioning rule for the new commit, and push again. The existing PR updates automatically.

The maintainer decides whether and when to merge. Merging incorporates the proposed changes into the base branch. Closing a PR without merging leaves them out. You can delete the contribution branch after the PR is merged or closed.
