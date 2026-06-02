import json, uuid, random, sys
from datetime import datetime, timezone, timedelta

rng = random.Random(42)
STORE_ID = 'ST1008'

ZONES_CAMS = [
    ('FACES_CANADA','CAM_FLOOR_01',45000),
    ('MINIMALIST','CAM_FLOOR_02',52000),
    ('GOOD_VIBES','CAM_FLOOR_02',38000),
    ('MARS_NYBAE','CAM_FLOOR_01',31000),
    ('DERMDOC','CAM_FLOOR_02',55000),
    ('COSRX_KOREAN','CAM_FLOOR_03',48000),
    ('ALPS_GOODNESS','CAM_FLOOR_01',22000),
    ('MAYBELLINE','CAM_FLOOR_01',26000),
    ('ACCESSORIES','CAM_FLOOR_01',18000),
    ('LAKME','CAM_FLOOR_01',24000),
    ('FOXTALE','CAM_FLOOR_02',30000),
    ('SWISS_BEAUTY','CAM_FLOOR_03',20000),
]

events = []
base = datetime(2026,4,10,4,30,0,tzinfo=timezone.utc)

for i in range(90):
    vid = f'VIS_{i:04d}'
    offset = timedelta(minutes=i*7 + rng.randint(0,5))
    ts = base + offset
    events.append({'event_id':str(uuid.uuid4()),'store_id':STORE_ID,'camera_id':'CAM_ENTRY_01',
        'visitor_id':vid,'event_type':'ENTRY','timestamp':ts.isoformat(),
        'zone_id':None,'dwell_ms':0,'is_staff':False,'confidence':round(rng.uniform(0.82,0.97),2),
        'metadata':{'session_seq':1}})
    nz = rng.randint(1,4)
    sample_zones = rng.sample(ZONES_CAMS, min(nz, len(ZONES_CAMS)))
    for seq,(zone,cam,dwell) in enumerate(sample_zones, 2):
        t2 = ts + timedelta(minutes=rng.randint(3,12))
        ad = int(dwell * rng.uniform(0.6,1.4))
        events.append({'event_id':str(uuid.uuid4()),'store_id':STORE_ID,'camera_id':cam,
            'visitor_id':vid,'event_type':'ZONE_ENTER','timestamp':t2.isoformat(),
            'zone_id':zone,'dwell_ms':0,'is_staff':False,'confidence':round(rng.uniform(0.82,0.97),2),
            'metadata':{'session_seq':seq}})
        events.append({'event_id':str(uuid.uuid4()),'store_id':STORE_ID,'camera_id':cam,
            'visitor_id':vid,'event_type':'ZONE_DWELL','timestamp':(t2+timedelta(seconds=30)).isoformat(),
            'zone_id':zone,'dwell_ms':ad,'is_staff':False,'confidence':round(rng.uniform(0.82,0.97),2),
            'metadata':{'session_seq':seq+1}})
    if i < 24:
        t3 = ts + timedelta(minutes=rng.randint(20,45))
        events.append({'event_id':str(uuid.uuid4()),'store_id':STORE_ID,'camera_id':'CAM_BILLING_01',
            'visitor_id':vid,'event_type':'BILLING_QUEUE_JOIN','timestamp':t3.isoformat(),
            'zone_id':'BILLING','dwell_ms':0,'is_staff':False,'confidence':round(rng.uniform(0.85,0.97),2),
            'metadata':{'queue_depth':rng.randint(1,5),'session_seq':10}})

batches = [events[i:i+100] for i in range(0,len(events),100)]
for idx,batch in enumerate(batches):
    fname = f'st1008_batch{idx}.json'
    with open(fname,'w') as f:
        json.dump({'events':batch},f)
    print(f'Batch {idx}: {len(batch)} events -> {fname}')
print(f'Total: {len(events)} events')
