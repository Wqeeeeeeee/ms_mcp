"""Shared, dependency-free DMol3 calculation contract constants."""

DMOL3_MATERIALSCRIPT_CONTRACT = "Materials Studio 20.1"
DMOL3_GEOMETRY_API_OBJECT = "Modules->DMol3->GeometryOptimization"
DMOL3_GEOMETRY_RESULT_SCHEMA = (
    "material_studio_dmol3_geometry_optimization_result_v1"
)
DMOL3_REVIEWED_RESULT_KEYS = (
    "Structure",
    "Report",
    "EnergyChart",
    "ConvergenceChart",
    "TotalEnergy",
    "Converged",
)
