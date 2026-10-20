"""Render downloaded PLATEAU building tiles in Blender, without AI treatment."""

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import local_point, tile_matrix
from gltf_compat import remove_external_rtc


def arguments():
    args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ground", type=Path)
    parser.add_argument("--terrain", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=2400)
    parser.add_argument("--samples", type=int, default=32)
    return parser.parse_args(args)


def import_tiles(manifest_path, data, lon, lat):
    import bpy
    from mathutils import Matrix

    imported = []
    cache = Path(".cache/blender-import")
    cache.mkdir(parents=True, exist_ok=True)
    identity = [int(r == c) for c in range(4) for r in range(4)]
    for tile in data["tiles"]:
        path = manifest_path.parent / tile["glb_path"]
        compatible_path = cache / path.name
        compatible_path.write_bytes(remove_external_rtc(path.read_bytes()))
        previous = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=str(compatible_path.resolve()))
        objects = set(bpy.data.objects) - previous
        matrix = Matrix(
            tile_matrix(
                tile.get("transform", identity),
                tile.get("rtc_center", [0, 0, 0]),
                lon,
                lat,
            )
        )
        roots = [obj for obj in objects if obj.parent not in objects]
        for obj in roots:
            obj.matrix_world = matrix @ obj.matrix_world
        bpy.context.view_layer.update()
        for obj in objects:
            if obj.type == "MESH":
                imported.append(obj)
        print("Imported", path.name, "meshes", len(imported), flush=True)
    return imported


def crop_objects(objects, bounds):
    import bmesh
    from mathutils import Matrix

    xmin, ymin, xmax, ymax = bounds
    planes = [
        ((xmin, 0, 0), (-1, 0, 0)),
        ((xmax, 0, 0), (1, 0, 0)),
        ((0, ymin, 0), (0, -1, 0)),
        ((0, ymax, 0), (0, 1, 0)),
    ]
    for obj in objects:
        world = obj.matrix_world.copy()
        obj.parent = None
        obj.data.transform(world)
        obj.matrix_world = Matrix.Identity(4)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        for point, normal in planes:
            bmesh.ops.bisect_plane(
                bm,
                geom=list(bm.verts) + list(bm.edges) + list(bm.faces),
                dist=0.0001,
                plane_co=point,
                plane_no=normal,
                clear_outer=True,
                clear_inner=False,
            )
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()


def bake_and_crop(objects, bounds):
    import bpy
    import numpy as np

    crop_objects(objects, bounds)
    zs = []
    vertex_count, triangle_count = 0, 0
    for obj in objects:
        coords = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
        obj.data.vertices.foreach_get("co", coords)
        if len(coords):
            zs.append(coords.reshape(-1, 3)[:, 2])
        vertex_count += len(obj.data.vertices)
        obj.data.calc_loop_triangles()
        triangle_count += len(obj.data.loop_triangles)
    if not zs:
        raise RuntimeError("No geometry inside the crop")
    heights = np.concatenate(zs)
    zmin, zmax = float(heights.min()), float(heights.max())
    if not (-100 < zmin < 300 and 100 < zmax < 600):
        raise RuntimeError(f"Implausible Shinjuku vertical bounds: {zmin}, {zmax}")
    bpy.context.view_layer.update()
    return {
        "vertices": vertex_count,
        "triangles": triangle_count,
        "z_min_ellipsoid_m": zmin,
        "z_max_ellipsoid_m": zmax,
        "display_ground_z_m": float(np.percentile(heights, 1)) - 0.3,
    }


def ground_mesh(bounds, height, ground_path, terrain_path, lon, lat):
    import bpy

    xmin, ymin, xmax, ymax = bounds
    mesh = bpy.data.meshes.new("Study ground")
    if terrain_path:
        data = json.loads(terrain_path.read_text())
        vertices = [
            local_point(x, y, z, lon, lat)
            for x, y, z in data["vertices_lon_lat_height"]
        ]
        faces = data["triangles"]
        name = "Ground: official PLATEAU ellipsoidal-height terrain"
    else:
        vertices = [
            (xmin, ymin, height),
            (xmax, ymin, height),
            (xmax, ymax, height),
            (xmin, ymax, height),
        ]
        faces = [(0, 1, 2, 3)]
        name = "Ground: flat display plane, not terrain"
    mesh.from_pydata(vertices, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    crop_objects([obj], bounds)
    material = bpy.data.materials.new("Ground orthophoto")
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Roughness"].default_value = 1
    shader.inputs["Base Color"].default_value = (0.55, 0.56, 0.55, 1)
    if ground_path and ground_path.exists():
        meta = json.loads(ground_path.read_text())
        w, s, e, n = meta["bounds_wsen"]
        sw, ne = local_point(w, s, 0, lon, lat), local_point(e, n, 0, lon, lat)
        uvs = mesh.uv_layers.new(name="Source map coordinates")
        for loop in mesh.loops:
            v = mesh.vertices[loop.vertex_index].co
            uvs.data[loop.index].uv = (
                (v.x - sw[0]) / (ne[0] - sw[0]),
                (v.y - sw[1]) / (ne[1] - sw[1]),
            )
        image_path = ground_path.parent / "ground.png"
        tex = material.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(str(image_path.resolve()))
        tex.interpolation = "Linear"
        material.node_tree.links.new(tex.outputs["Color"], shader.inputs["Base Color"])
        material.node_tree.links.new(
            tex.outputs["Color"], shader.inputs["Emission Color"]
        )
        shader.inputs["Emission Strength"].default_value = 0.25
    mesh.materials.append(material)
    return obj


def setup_scene(args, ground_height):
    import bpy
    from mathutils import Vector

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = args.samples
    scene.cycles.use_denoising = False
    scene.render.resolution_x = args.resolution
    scene.render.resolution_y = args.resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    world = bpy.data.worlds.new("Neutral studio environment")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.82, 0.86, 0.9, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.8
    scene.world = world
    bpy.ops.object.light_add(type="SUN", location=(-100, -200, 900))
    sun = bpy.context.object
    sun.name = "Neutral directional light"
    sun.data.energy = 1.2
    sun.data.angle = math.radians(12)
    sun.rotation_euler = (math.radians(20), math.radians(-25), math.radians(-35))
    bpy.ops.object.camera_add()
    camera = bpy.context.object
    camera.name = "Orthographic isometric camera"
    camera.data.type = "ORTHO"
    camera.data.clip_start = 1
    camera.data.clip_end = 10000
    scene.camera = camera
    target = Vector((0, 0, ground_height + 55))
    camera.location = target + Vector((1500, -1500, 1500))
    camera.rotation_euler = (
        (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    )
    camera.data.ortho_scale = 1260
    return scene, camera


def render_view(scene, path):
    import bpy

    scene.render.filepath = str(path.resolve())
    bpy.ops.render.render(write_still=True)


def main():
    import bpy
    from mathutils import Vector

    args = arguments()
    args.output.mkdir(parents=True, exist_ok=True)
    data = json.loads(args.manifest.read_text())
    lon, lat = data["origin"]["lon"], data["origin"]["lat"]
    w, s, e, n = data["bbox"]
    sw, ne = local_point(w, s, 0, lon, lat), local_point(e, n, 0, lon, lat)
    bounds = (sw[0], sw[1], ne[0], ne[1])
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    objects = import_tiles(args.manifest, data, lon, lat)
    stats = bake_and_crop(objects, bounds)
    ground = ground_mesh(
        bounds, stats["display_ground_z_m"], args.ground, args.terrain, lon, lat
    )
    scene, camera = setup_scene(args, stats["display_ground_z_m"])
    print("RENDER_STATS", json.dumps(stats), flush=True)
    render_view(scene, args.output / "shinjuku-isometric.png")

    # Same source, closer camera: no geometry or texture substitution.
    target = Vector((-10, -60, stats["display_ground_z_m"] + 110))
    camera.location = target + Vector((1500, -1500, 1500))
    camera.rotation_euler = (
        (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    )
    camera.data.ortho_scale = 530
    render_view(scene, args.output / "shinjuku-detail.png")

    # Texture-free diagnostic, identical geometry and overview viewpoint.
    target = Vector((0, 0, stats["display_ground_z_m"] + 55))
    camera.location = target + Vector((1500, -1500, 1500))
    camera.rotation_euler = (
        (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    )
    camera.data.ortho_scale = 1260
    bpy.ops.file.pack_all()
    bpy.data.texts.new("SOURCE_NOTES.txt").write(
        "Buildings: Project PLATEAU, Shinjuku FY2025. Cropped and rendered. No AI styling.\n"
        "https://www.geospatial.jp/ckan/dataset/plateau-13104-shinjuku-ku-2025\n"
        f"Ground: PLATEAU-Ortho 2023. {ground.name}.\n"
        "https://docs.plateauview.mlit.go.jp/datasets/ortho/\n"
        "Terrain attribution: PLATEAU | Mapterhorn | 国土地理院\n"
        "https://docs.plateauview.mlit.go.jp/datasets/terrain/\n"
        "Licence: https://www.mlit.go.jp/plateau/site-policy/\n"
        "Not an official PLATEAU render. Dataset edition is not imagery capture date.\n"
    )
    bpy.ops.wm.save_as_mainfile(
        filepath=str((args.output / "shinjuku-source.blend").resolve())
    )
    clay = bpy.data.materials.new("Geometry diagnostic override")
    clay.diffuse_color = (0.7, 0.72, 0.73, 1)
    clay.use_nodes = True
    clay.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
        0.7,
        0.72,
        0.73,
        1,
    )
    clay.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1
    scene.view_layers[0].material_override = clay
    render_view(scene, args.output / "shinjuku-geometry.png")
    scene.view_layers[0].material_override = None
    stats.update(
        origin_lonlat=[lon, lat],
        crop_wsen=[w, s, e, n],
        horizontal_extent_m=[bounds[2] - bounds[0], bounds[3] - bounds[1]],
        camera="Orthographic, equal XYZ view direction, 35.264 degrees above horizon",
        ground=ground.name + ", draped with PLATEAU-Ortho 2023",
        ai_styling=False,
        render_engine="Blender Cycles CPU, Standard colour management, no denoising",
        source_manifest=str(args.manifest.resolve()),
        ground_object=ground.name,
        terrain_vertices=len(ground.data.vertices),
        terrain_triangles=len(ground.data.polygons),
        resolution_px=args.resolution,
        samples=args.samples,
    )
    (args.output / "render-metadata.json").write_text(
        json.dumps(stats, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
