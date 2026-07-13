# Benzene To Nitrobenzene Workflow

1. Create benzene from `src/material_studio_mcp_server/examples/benzene_spec.json` with `execution_mode=preview`.
2. Apply a semantic patch that deletes `H1`, adds `N1`, `O1`, and `O2`, and bonds `C1-N1`, `N1-O1`, and `N1-O2`.
3. Preview the updated Forcite optimization script.
4. Execute only after explicit confirmation.
