from .app import create_app
app = create_app()
try:
    import numpy as np
    from pathlib import Path
    from .batch_physics import ResidentPhysics
    from .physics_service import PhysicsQueue
    resident = ResidentPhysics(environments=4, capacity=4)
    print('DIAG_PATHS', resident.paths, 'SLOTS', resident.slots.tolist(), flush=True)
    print('DIAG_INITIAL', resident.rigids.get_transforms().numpy().tolist(), flush=True)
    state = PhysicsQueue(Path('results/batch-diagnostic'))
    requests = [state.prepare({'bodies':[{'id':'drop', 'position':[float(i),0,2]}], 'steps':30}) for i in range(4)]
    for mode in [False, False, True, True]:
        values, timing = resident.simulate(requests, tensor_step=mode)
        print('DIAG_RESULT', mode, timing, [(p.tolist(),v.tolist()) for p,v in values], flush=True)
        print('DIAG_DISABLE_GRAVITY', resident.rigids.get_disable_gravities().numpy().reshape(-1).tolist(), flush=True)
    resident.close()
finally:
    app.close()
