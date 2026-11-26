// Continuous wind with soft local separation. Coordinates are LED pixels.
// Layer order and opacity never participate in avoidance.
export const MOTION = Object.freeze({ loop:3400, minY:185, bottom:742,
  maxSideSpeed:4.5, speedAllowance:2.5, maxAcceleration:.55, lookAhead:12 });
const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));
const wrapped = x => ((x % MOTION.loop) + MOTION.loop) % MOTION.loop;
const distanceX = x => wrapped(x + MOTION.loop / 2) - MOTION.loop / 2;
const smooth = x => { const t = clamp(x,0,1); return t*t*(3-2*t); };

export function reconcileCloudMotion(clouds, previous = new Map()) {
  return new Map(clouds.map(cloud => {
    const old = previous.get(cloud.id);
    const same = old?.content === cloud.content;
    return [cloud.id, { ...cloud, x:same?old.x:cloud.x, y:same?old.y:cloud.y,
      homeY:same?old.homeY:cloud.y, vx:same?old.vx:cloud.speed, vy:same?old.vy:0,
      age:same?old.age:0, layer:same?old.layer:Math.round(cloud.y) }];
  }));
}

// Mutates the simulation only, never the fixture or React props. Small steps
// cap acceleration, including when several neighbours enter range together.
export function advanceCloudMotion(states, seconds) {
  if (!(seconds > 0)) return states;
  const count = Math.ceil(seconds / (1/30));
  const dt = seconds / count;
  const clouds = [...states.values()];
  for (let step=0; step<count; step++) {
    const steering = clouds.map(cloud => ({
      x:0, y:(cloud.homeY-cloud.y)*.008 + Math.sin(cloud.age/(17+cloud.seed%19)+cloud.seed)*.15,
    }));
    for (let i=0; i<clouds.length; i++) for (let j=i+1; j<clouds.length; j++) {
      const a=clouds[i], b=clouds[j];
      const dx=distanceX(a.x+a.width/2-b.x-b.width/2);
      const dv=a.vx-b.vx;
      const future=Math.abs(dv)>.02?clamp(-dx/dv,0,MOTION.lookAhead):0;
      const ahead=dx+dv*future;
      const dy=a.y+a.height*.58-b.y-b.height*.58;
      const rx=(a.width+b.width)*.42+14;
      const ry=(a.height+b.height)*.32+12;
      if (Math.abs(ahead)>rx*1.35 || Math.abs(dy)>ry*1.35) continue;
      const proximity=Math.hypot(ahead/rx,dy/ry);
      const pressure=smooth((1.35-proximity)/.95);
      if (!pressure) continue;
      // Preserve the side already taken. Stable IDs break exact ties; array
      // order, arriving posts and frame rate cannot reverse that choice.
      const tie=String(a.id)<String(b.id)?-1:1;
      const vertical=Math.abs(dy)>1?Math.sign(dy):tie;
      const horizontal=Math.abs(dx)>1?Math.sign(dx):tie;
      steering[i].y+=vertical*pressure*4.2;
      steering[j].y-=vertical*pressure*4.2;
      steering[i].x+=horizontal*pressure*1.7;
      steering[j].x-=horizontal*pressure*1.7;
    }
    for (let i=0; i<clouds.length; i++) {
      const cloud=clouds[i];
      const minY=MOTION.minY, maxY=MOTION.bottom-cloud.height;
      const targetX=cloud.speed+clamp(steering[i].x,-MOTION.speedAllowance,MOTION.speedAllowance);
      // Slow before the edge, so a neighbour cannot push a cloud off the sky.
      const targetY=clamp(steering[i].y,
        -Math.min(MOTION.maxSideSpeed,Math.max(0,(cloud.y-minY)*.09)),
        Math.min(MOTION.maxSideSpeed,Math.max(0,(maxY-cloud.y)*.09)));
      const ax=(targetX-cloud.vx)/3.5, ay=(targetY-cloud.vy)/3.5;
      const factor=Math.min(1,MOTION.maxAcceleration/Math.max(.000001,Math.hypot(ax,ay)));
      cloud.vx+=ax*factor*dt; cloud.vy+=ay*factor*dt;
      cloud.x=wrapped(cloud.x+cloud.vx*dt);
      cloud.y=clamp(cloud.y+cloud.vy*dt,minY,maxY);
      // The clamp is a final numerical guard, outside the normal soft braking.
      if (cloud.y===minY && cloud.vy<0 || cloud.y===maxY && cloud.vy>0) cloud.vy=0;
      cloud.age+=dt;
    }
  }
  return states;
}
