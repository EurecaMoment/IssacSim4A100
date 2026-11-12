"""Fetch the dependency closure of PhysX, without RTX renderer extensions."""
from collections import deque
import os
import json
from pathlib import Path
import sys
import tomllib
import zipfile
from range_wheel import RangeFile

base = Path(os.environ.get("A100SIM_ROOT", Path(__file__).resolve().parent.parent))
destination = base / 'packages'
package = 'isaacsim-extscache-kit'
filename = package.replace('-', '_') + '-6.2.0.0-cp312-none-manylinux_2_35_x86_64.whl'
source = RangeFile('https://pypi.nvidia.com/' + package + '/' + filename)
source.block = 4 * 1024 * 1024

def installed():
    result = {}
    for folder in [destination/'isaacsim/exts', destination/'isaacsim/extsDeprecated',
                   destination/'isaacsim/extscache', destination/'isaacsim/kit/extscore']:
        for manifest in folder.glob('*/config/extension.toml'):
            try:
                value = tomllib.loads(manifest.read_text())
                name = manifest.parent.parent.name.split('-')[0]
                result[name] = value
            except Exception as exc: print('manifest warning', str(manifest), repr(exc), flush=True)
    return result

with zipfile.ZipFile(source) as archive:
    files = archive.infolist()
    manifests = {item.filename.split('/')[2].split('-')[0]: item for item in files
                 if item.filename.startswith('isaacsim/extscache/') and item.filename.endswith('/config/extension.toml')}
    registry = installed()
    initial = sys.argv[1:] or ['omni.physx', 'omni.physx.tensors', 'omni.physx.fabric',
                              'omni.physics.physx', 'omni.physics.stageupdate', 'omni.kit.loop',
                              'omni.warp.core', 'isaacsim.simulation_app', 'omni.usd',
                              'omni.kit.usd.layers', 'omni.hydra.usdrt_delegate']
    pending = deque(initial); visited = set(); selected = {}; missing = []
    while pending:
        name = pending.popleft()
        if name in visited: continue
        visited.add(name)
        if any(x in name for x in ('hydra.rtx', 'rtx.spg', 'sensors.nv')):
            raise RuntimeError('RTX dependency unexpectedly requested: ' + name)
        if name in registry: value = registry[name]
        elif name in manifests:
            item = manifests[name]; value = tomllib.loads(archive.read(item).decode('utf8'))
            selected[name] = item.filename.rsplit('/config/', 1)[0] + '/'
        else:
            missing.append(name); print('MISSING', name, flush=True); continue
        def dependencies(table):
            for dependency, options in table.items():
                if dependency.startswith('filter:'):
                    if dependency in ('filter:os','filter:platform','filter:config'):
                        for target, nested in options.items():
                            if target in ('linux','linux-x86_64','release'):
                                yield from dependencies(nested)
                    elif dependency=='filter:setting':
                        continue
                    else:
                        yield from dependencies(options)
                elif isinstance(options,dict) and not options.get('optional'):
                    yield dependency
        pending.extend(dependencies(value.get('dependencies', {})))
        print('resolved', name, flush=True)
    required = [item for item in files if any(item.filename.startswith(prefix) for prefix in selected.values())]
    total = sum(item.compress_size for item in required)
    print('extensions', len(selected), 'compressed MiB', round(total/1024**2,1), 'missing', missing, flush=True)
    receipt = {'requested': initial, 'resolved': sorted(visited), 'downloaded_extensions': sorted(selected), 'missing': missing}
    for item in sorted(required, key=lambda item:item.header_offset):
        target = destination / item.filename
        if not target.resolve().is_relative_to(destination.resolve()): raise ValueError('Invalid wheel path')
        if item.is_dir(): target.mkdir(parents=True, exist_ok=True); continue
        if target.is_file() and target.stat().st_size == item.file_size: continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(item))
        if item.filename.endswith('/config/extension.toml'): print('extracted', item.filename, flush=True)
    receipt['https_bytes_received'] = source.downloaded
    (base/'downloads').mkdir(exist_ok=True)
    (base/'downloads'/'physics-extensions.json').write_text(json.dumps(receipt,indent=2))
    print('EXTENSIONS READY; HTTPS MiB',round(source.downloaded/1024**2,1),flush=True)

    if missing:
        raise RuntimeError('Required extensions were not resolved: ' + ', '.join(missing))
