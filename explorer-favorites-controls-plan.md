# Explorer favorites controls

Move the favorites filter beside the workspace toggle in the explorer location controls. Workspace selects the current folder; favorites filters within that folder. Preserve favorites when navigating between workspace and vault root, and preserve the folder when switching favorites off. Keep the existing behavior of opening a favorite folder to browse its ordinary contents.

## Implementation and validation

- Render the favorites toggle from the toolbar's current options beside the workspace toggle, removing its old placement beside Close.
- Keep the options as the source of truth so toolbar refreshes preserve the filter's pressed state.
- Extend an existing real browser workspace scenario to verify combined filtering, navigation in both directions, and the current folder after removing the favorites filter. Assert visible file identities and toggle state, not UI text or DOM layout.
- Run the affected browser scenarios and JavaScript syntax checks; update the canonical admission hashes. Do not run the full suite.

## Documentation review

No protected documentation changes are needed for this local control placement and existing filter composition. Discuss any later documentation draft and obtain explicit approval before editing protected files.
