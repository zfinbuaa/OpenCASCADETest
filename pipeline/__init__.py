# Pipeline package - OCCT STEP → glTF + assembly.json

# Phase 0: Data I/O
from .stp_reader import read_stp, read_stp_with_doc, verify_doc
from .mesher import brep_to_mesh, get_mesh_stats
from .xcaf_utils import extract_assembly_tree, flatten_assembly_tree, get_tree_stats
from .gltf_exporter import export_merged_glb, export_assembly_indexed

# Phase 1: Assembly analysis
from .contact_detector import detect_contacts, get_contact_graph
from .fastener_identifier import identify_fasteners, identify_fasteners_detailed
from .dag_builder import build_disassembly_dag, build_disassembly_dag_v2, assign_stages_to_parts
from .direction_calc import calc_disassembly_direction, compute_all_directions
from .assembly_json import build_assembly_json, write_assembly_json

# Phase 2: Collision & path validation
from .collision_check import check_disassembly_path, check_obstacle_set, prepare_collision_data, find_best_feasible_direction
from .path_searcher import find_feasible_direction, compute_all_feasible_directions
from .path_validator import validate_disassembly_plan, generate_report
