# Graphene Vacancy CASTEP Workflow

1. Create the graphene vacancy example with `execution_mode=preview`.
2. Apply CASTEP settings with cutoff `400 eV`.
3. Modify the cutoff to `520 eV` through a semantic patch.
4. Use `material_studio_project_rollback` to create a non-destructive rollback revision if needed.
