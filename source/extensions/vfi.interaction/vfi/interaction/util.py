import omni.kit.commands
import omni.kit.app
import carb.events
import omni.usd
from pxr import Usd, Gf, UsdGeom
import carb
import json
import omni
from omni.kit.viewport.utility import get_active_viewport_window


def get_world_position(path: str):
    """Get the world position of the prim.

    Args:
            path: Path of a prim.

    Returns:
            Gf.Vec3d: The world position of the prim.
    """
    world_transform = omni.usd.get_world_transform_matrix(path)
    world_position = world_transform.ExtractTranslation()
    return world_position

def get_scene_up_axis():
    """Get the current scene's up axis.

    Returns:
        str: 'Y' or 'Z' indicating the up axis
    """
    stage = omni.usd.get_context().get_stage()
    up_axis = UsdGeom.GetStageUpAxis(stage)
    return up_axis  # Return 'Y' or 'Z'

def compute_bbox(prim: Usd.Prim) -> Gf.Range3d:
    """
    Compute Bounding Box using ComputeWorldBound at UsdGeom.Imageable
    See https://openusd.org/release/api/class_usd_geom_imageable.html

    Args:
        prim: A prim to compute the bounding box.
    Returns:
        A range (i.e. bounding box), see more at: https://openusd.org/release/api/class_gf_range3d.html
    """
    imageable = UsdGeom.Imageable(prim)
    time = Usd.TimeCode.Default() # The time at which we compute the bounding box
    bound = imageable.ComputeWorldBound(time, UsdGeom.Tokens.default_)
    bound_range = bound.ComputeAlignedBox()
    return bound_range

def get_prim_full_transform(prim_path):
    """Get the prim transform, converting to Y-up if scene is Z-up
    and converting from Omniverse units (cm) to RealityKit units (m).

    Args:
        prim_path (str): Path to the prim

    Returns:
        list: Camera transform matrix elements in RealityKit coordinate system (meters)
    """

    # Get stage and camera prim
    stage = omni.usd.get_context().get_stage()
    prim = stage.GetPrimAtPath(prim_path)
    if not prim:
        return None

    # Get camera transform using UsdGeom
    camera_xformable = UsdGeom.Xformable(prim)
    world_transform = camera_xformable.ComputeLocalToWorldTransform(Usd.TimeCode.Default())

    # Convert to Gf.Matrix4d for easier manipulation
    ov_matrix = Gf.Matrix4d(world_transform)

    up_axis = get_scene_up_axis()

    # Create the output matrix
    result_matrix = Gf.Matrix4d()

    # Extract components from original matrix
    raw_translation = ov_matrix.ExtractTranslation()
    translation = needs_cm_to_m_conversion(stage, raw_translation)
    rotation = ov_matrix.RemoveScaleShear()

    if not hasattr(get_prim_full_transform, '_logged'):
        get_prim_full_transform._logged = True
        carb.log_warn(f"[DIAG] Camera prim: {prim_path}")
        carb.log_warn(f"[DIAG] Camera raw translation (stage units): {raw_translation}")
        carb.log_warn(f"[DIAG] Camera translation (meters): {translation}")
        carb.log_warn(f"[DIAG] Scene up axis: {up_axis}")

    if up_axis == UsdGeom.Tokens.z:
        # For Z-up, we need a coordinate conversion
        # X → X, Y → Z, Z → -Y

        # Convert translation to meters and switch axes
        result_translation = Gf.Vec3d(
            translation[0],      # X stays as X
            translation[2],     # -Z becomes Y
            -translation[1]       # Y becomes Z
        )

        # Create rotation conversion matrix (Z-up to Y-up)
        conversion = Gf.Matrix4d(
            1.0, 0.0, 0.0, 0.0,   # X remains X
            0.0, 0.0, -1.0, 0.0,   # Y becomes Z
            0.0, 1.0, 0.0, 0.0,  # Z becomes -Y
            0.0, 0.0, 0.0, 1.0
        )

        # Apply conversion to rotation
        result_rotation = rotation * conversion
    else:
        # For Y-up, no axis conversion needed
        result_translation = Gf.Vec3d(
            translation[0],
            translation[1],
            translation[2]
        )
        result_rotation = rotation

    # Set the translation in the result matrix
    result_matrix = result_rotation
    result_matrix.SetTranslateOnly(result_translation)

    if not hasattr(get_prim_full_transform, '_logged_result'):
        get_prim_full_transform._logged_result = True
        carb.log_warn(f"[DIAG] Result translation (Y-up, meters): {result_translation}")
        carb.log_warn(f"[DIAG] Result matrix row3 (translation row): {result_matrix[3][0]}, {result_matrix[3][1]}, {result_matrix[3][2]}, {result_matrix[3][3]}")

    # Serialize: output USD row-major, which RealityKit reads as column-major.
    # This correctly transposes the row-vector convention (USD) to column-vector
    # convention (RealityKit/simd), placing translation in simd column 3.
    matrix = []
    for col in range(4):
        for row in range(4):
            matrix.append(result_matrix[col][row])

    return matrix

def get_camera_transform():
    """Get the current viewport camera transform, converting to Y-up if scene is Z-up
    and converting from Omniverse units (cm) to RealityKit units (m).

    Returns:
        list: Camera transform matrix elements in RealityKit coordinate system (meters)
    """
    viewport_window = get_active_viewport_window()
    if not viewport_window:
        return None

    viewport_api = viewport_window.viewport_api
    camera_path = viewport_api.get_active_camera()
    if not camera_path:
        return None

    matrix = get_prim_full_transform(camera_path)

    return matrix

def calculate_bounding_box_info(prim_path, is_z_up=False):
    """Calculate bounding box information for a prim.

    Args:
        prim_path (str): Path to the prim
        is_z_up (bool): Whether the stage uses Z-up coordinate system

    Returns:
        tuple: (prim, bbox_info_string) where bbox_info_string contains formatted bbox data
    """
    stage = omni.usd.get_context().get_stage()

    with Usd.EditContext(stage, stage.GetSessionLayer()):
        prim = stage.GetPrimAtPath(prim_path)
        if not prim:
            carb.log_error(f"Prim does not exist on stage: {prim_path}")
            return None, ""

        # Compute the bounding box
        # Original Y-up calculations
        bbox = compute_bbox(prim)
        bbox_min = needs_cm_to_m_conversion(stage, bbox.GetMin())
        bbox_max = needs_cm_to_m_conversion(stage, bbox.GetMax())
        bbox_center = needs_cm_to_m_conversion(stage, bbox.GetMidpoint())

        bbox_world_position = get_world_position(prim_path)
        bbox_world_position = needs_cm_to_m_conversion(stage, bbox_world_position)
        bbox_dimentions = tuple(map(lambda min, max: max - min, bbox_min, bbox_max))

        # Calculate box dimensions and center position
        if is_z_up:
            # Swap Y and Z coordinates to convert from Z-up to Y-up
            bbox_dimentions = (bbox_dimentions[0], bbox_dimentions[2], bbox_dimentions[1])
            bbox_center = (bbox_center[0], bbox_center[2], -bbox_center[1])

            # Get world position and swap coordinates
            bbox_world_position = (bbox_world_position[0], bbox_world_position[2], -bbox_world_position[1])

        # Format the bbox info as a string
        box_values = (
            bbox_dimentions[0],
            bbox_dimentions[1],
            bbox_dimentions[2],
            bbox_center[0],
            bbox_center[1],
            bbox_center[2],
            bbox_world_position[0],
            bbox_world_position[1],
            bbox_world_position[2],
        )
        box_string = str(box_values)[1:-1]  # Remove parentheses

        return prim, box_string

def convert_transformation_matrix(transformation_str, is_z_up=False):
    """Convert a transformation matrix from client format to USD format.

    Args:
        transformation_str (str): Comma-separated matrix values
        is_z_up (bool): Whether to convert between Y-up and Z-up coordinate systems

    Returns:
        Gf.Matrix4d: Converted transformation matrix
    """
    # Decode the transformation matrix
    flattened_matrix_str = transformation_str.split(",")
    flattened_matrix_list = [float(value) for value in flattened_matrix_str]

    # Create a 4x4 matrix from the flattened list (column-major order)
    matrix = []
    for i in range(0, 12, 3):
        # Each column has 3 values (we'll add the 4th value)
        column = flattened_matrix_list[i:i+3] + [0.0 if i < 9 else 1.0]
        matrix.append(column)


    if is_z_up:
        # Create proper conversion matrix for rotations between Y-up and Z-up systems
        # We need to use a coordinate system conversion matrix

        # Y-up to Z-up conversion matrix
        # [ 1  0  0  0 ]   (X stays X)
        # [ 0  0  1  0 ]   (Y becomes Z)
        # [ 0 -1  0  0 ]   (Z becomes -Y)
        # [ 0  0  0  1 ]

        # Build the incoming matrix from client (in Y-up)
        client_matrix = Gf.Matrix4d(matrix)

        # Build the conversion matrix (Y-up to Z-up)
        convert_y_to_z = Gf.Matrix4d(
            1.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, -1.0, 0.0, 0.0,
            0.0, 0.0, 0.0, 1.0
        )

        # Convert Z-up to Y-up (inverse of above)
        convert_z_to_y = convert_y_to_z.GetInverse()

        # Apply the coordinate system conversion:
        # Convert from client's Y-up to Omniverse's Z-up
        return convert_z_to_y * client_matrix * convert_y_to_z

    else:
        # Original Y-up calculations - no conversion needed
        return Gf.Matrix4d(matrix)

def apply_transformation_to_prim(prim_path, transformation_matrix):
    """Apply a transformation matrix to a prim.

    Args:
        prim_path (str): Path to the prim
        transformation_matrix (Gf.Matrix4d): Transformation matrix to apply

    Returns:
        bool: True if successful, False otherwise
    """
    stage = omni.usd.get_context().get_stage()
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        prim = stage.GetPrimAtPath(prim_path)
        if not prim:
            carb.log_error(f"Prim does not exist on stage: {prim_path}")
            return False

        # Get the xformable
        xformable = UsdGeom.Xformable(prim)

        # Check if there's already a transform op
        existing_ops = xformable.GetOrderedXformOps()
        transform_op = None

        # Look for an existing transform op
        for op in existing_ops:
            if op.GetOpType() == UsdGeom.XformOp.TypeTransform:
                transform_op = op
                break

        # If no transform op exists, create one and add it to the beginning of the op order
        # (In USD, operations are applied from right to left, so the rightmost operation
        # in the list is applied first to the geometry)
        if not transform_op:
            transform_op = xformable.AddTransformOp(UsdGeom.XformOp.PrecisionDouble, "transform")

            # Get the current xform op order
            op_order = xformable.GetOrderedXformOps()

            # If there are existing ops, add the transform op at the end (applied first)
            if op_order and len(op_order) > 1:  # > 1 because we just added the transform_op
                # Create a new op order with transform_op at the end
                new_op_order = [transform_op] + [op for op in op_order if op != transform_op]
                xformable.SetXformOpOrder(new_op_order, False)

        # Set the transformation matrix
        transform_op.Set(transformation_matrix)
        return True

def send_message_to_client(message):
    """Send a message to the CloudXR client.

    Args:
        message (dict or str): Message to send (will be converted to JSON if it's a dict)
    """
    if isinstance(message, dict):
        message_str = json.dumps(message)
    else:
        message_str = message

    omni.kit.app.queue_event("omni.kit.cloudxr.send_message", payload={"message": message_str})

def convert_client_delta_to_stage(dx, dy, dz, stage):
    """Convert a delta vector from client coords (Y-up, meters) to stage coords.

    This is the inverse of the Z-up->Y-up conversion in get_prim_full_transform.

    Args:
        dx, dy, dz: Delta in client space (Y-up, meters)
        stage: USD stage
    Returns:
        Gf.Vec3d in stage coordinate system and units
    """
    is_z_up = UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)

    # Convert meters to stage units (e.g. if stage is cm, scale = 100)
    scale = 1.0 / meters_per_unit
    sdx, sdy, sdz = dx * scale, dy * scale, dz * scale

    if is_z_up:
        # Inverse of get_prim_full_transform's X->X, Z->Y, -Y->Z
        # Client Y-up to Stage Z-up: X->X, Y->Z, Z->-Y
        return Gf.Vec3d(sdx, -sdz, sdy)
    else:
        return Gf.Vec3d(sdx, sdy, sdz)


def needs_cm_to_m_conversion(stage, value):
    """Convert value from cm to m if stage uses cm as units.

    Args:
        stage: USD stage
        value: Value to potentially convert
    Returns:
        Converted value if needed, original value otherwise
    """
    return cm_to_m(value) if UsdGeom.GetStageMetersPerUnit(stage) == 0.01 else value

def cm_to_m(value):
    """Convert centimeters to meters.

    Args:
        value (float): Value in centimeters
    Returns:
        float: Value in meters
    """
    return (value[0] / 100.0, value[1] / 100.0, value[2] / 100.0)
