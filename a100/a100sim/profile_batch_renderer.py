import cProfile,pstats
from .batch_renderer import BatchRenderer
import numpy as np
renderer=BatchRenderer()
requests=[{'bodies':[{'color':[.35,.5,.7]} for _ in range(64)],'render':{'azimuth':35+i*.1,'elevation':20,'target':[0,0,.5],'distance':5,'spp':8,'seed':42}} for i in range(64)]
poses=np.array([[(i%8-3.5)*.4,(i//8-3.5)*.4,.1,0,0,0,1] for i in range(64)],np.float32)
states=[(poses,np.zeros((64,6),np.float32))]*64
renderer.render(requests,states)
prof=cProfile.Profile();prof.enable()
for i in range(3):renderer.render(requests,states)
prof.disable();pstats.Stats(prof).sort_stats('cumulative').print_stats(25)
