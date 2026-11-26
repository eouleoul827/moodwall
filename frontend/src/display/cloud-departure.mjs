const sequence = value => /^\d+$/.test(String(value)) ? BigInt(value) : null;

export function departureReason(cloud, pool, hasArrival, wallNow) {
  const deadline=Date.parse(cloud.item?.contentDeleteAt);
  // The visible wall clock refreshes once a second; removal is authoritative.
  if (Number.isFinite(deadline) && deadline<=wallNow+1000) return 'expired';
  if (!hasArrival || pool.length<30) return null;
  const old=sequence(cloud.item?.completedSequence);
  const first=pool.reduce((min,item)=>{
    const next=sequence(item.completedSequence);
    return next!==null && (min===null || next<min)?next:min;
  },null);
  return old!==null && first!==null && old<first?'capacity':null;
}

export function createDeparture(cloud, reason, started) {
  // Whitelist geometry only. No message body, wrapped text or original item is
  // retained for an exit animation after a privacy/retention boundary.
  return { id:cloud.id, seed:cloud.seed, width:cloud.width, height:cloud.height,
    font:cloud.font, shape:cloud.shape, flipped:cloud.flipped,
    x:cloud.x, y:cloud.y, layer:cloud.layer,
    item:{ id:cloud.id, sceneId:cloud.item?.sceneId }, lines:[],
    reason, started, duration:reason==='capacity'?1200:900 };
}
