import React from 'react';
import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import RoundedCloudField from './RoundedCloudField.jsx';

const now=Date.parse('2026-11-26T10:00:00Z');
const item=(id, extra={})=>({id:String(id),content:`구름 ${id}`,sceneId:'support',completedSequence:String(id),
  contentDeleteAt:new Date(now+60_000).toISOString(),...extra});
const draw=(bubbles,extra={})=><RoundedCloudField view={{visible:true,bubbles}} serverNow={now} {...extra}/>;
beforeEach(()=>{vi.useFakeTimers();vi.setSystemTime(now);});
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();});

describe('retained clouds and departures',()=>{
  it('immediately purges expired text while a body-free silhouette dissolves once',()=>{
    const old=item(1,{content:'만료되면 남기지 않을 글',contentDeleteAt:new Date(now).toISOString()});
    const ui=render(draw([old]));
    expect(ui.getByLabelText(old.content)).toBeTruthy();
    ui.rerender(draw([]));
    expect(ui.container.innerHTML).not.toContain(old.content);
    const ghost=ui.container.querySelector('[data-departure="expired"]');
    expect(ghost).toBeTruthy(); expect(ghost.getAttribute('aria-hidden')).toBe('true');
    expect(ghost.querySelector('p')).toBeNull();
    expect(ui.container.querySelector('[data-message-count]').dataset.messageCount).toBe('0');
    act(()=>vi.advanceTimersByTime(901));
    expect(ui.container.querySelector('.rw-cloud-departure')).toBeNull();
  });
  it('removes the oldest body on the thirty-first completion without moving existing clouds',()=>{
    const pool=Array.from({length:30},(_,i)=>item(i+1));
    const ui=render(draw(pool));
    const survivor=ui.container.querySelector('[data-cloud-track="2"]');
    const x=survivor.style.getPropertyValue('--rw-x'),y=survivor.style.getPropertyValue('--rw-y');
    ui.rerender(draw([...pool.slice(1),item(31)]));
    expect(ui.queryByLabelText('구름 1')).toBeNull();
    expect(ui.getByLabelText('구름 31')).toBeTruthy();
    expect(ui.container.querySelectorAll('.rw-reading-cloud')).toHaveLength(30);
    expect(ui.container.querySelector('[data-departure="capacity"] p')).toBeNull();
    expect(ui.container.querySelector('[data-departure="capacity"]')).toBeTruthy();
    expect(ui.container.querySelector('[data-cloud-track="2"]')).toBe(survivor);
    expect(survivor.style.getPropertyValue('--rw-x')).toBe(x);
    expect(survivor.style.getPropertyValue('--rw-y')).toBe(y);
    act(()=>vi.advanceTimersByTime(1201));
    expect(ui.container.querySelector('.rw-cloud-departure')).toBeNull();
  });
  it('does not animate a moderation removal or disconnected wall',()=>{
    const ui=render(draw([item(1)]));
    ui.rerender(draw([]));
    expect(ui.container.querySelector('.rw-cloud-departure')).toBeNull();
    ui.rerender(draw([item(2,{contentDeleteAt:new Date(now).toISOString()})]));
    ui.rerender(draw([]));
    expect(ui.container.querySelector('.rw-cloud-departure')).toBeTruthy();
    ui.rerender(draw([],{view:{visible:false,bubbles:[]}}));
    expect(ui.container.innerHTML).toBe('');
    ui.rerender(draw([]));
    expect(ui.container.querySelector('.rw-cloud-departure')).toBeNull();
  });
  it('keeps continuous movement and fixed layering across pause and theme changes',()=>{
    let frame,requests=0;
    vi.spyOn(window,'requestAnimationFrame').mockImplementation(callback=>{frame=callback;return ++requests;});
    vi.spyOn(window,'cancelAnimationFrame').mockImplementation(()=>{frame=null;});
    const ui=render(draw([item(1),item(2)]));
    const track=ui.container.querySelector('.rw-cloud-track');
    const start=Number(track.style.getPropertyValue('--rw-x')),layer=track.style.zIndex;
    act(()=>{frame(0);for(let t=16;t<3000;t+=16)frame(t);});
    const moved=Number(track.style.getPropertyValue('--rw-x'));
    expect(moved).toBeGreaterThan(start);expect(track.style.opacity).toBe('');expect(track.style.zIndex).toBe(layer);
    ui.rerender(draw([item(1,{sceneId:'joy'}),item(2)],{paused:true}));
    expect(Number(track.style.getPropertyValue('--rw-x'))).toBe(moved);expect(frame).toBeNull();
    ui.rerender(draw([item(1,{sceneId:'joy'}),item(2)]));
    act(()=>frame(10_000));
    expect(Number(track.style.getPropertyValue('--rw-x'))).toBe(moved);
    act(()=>frame(10_016));
    expect(Number(track.style.getPropertyValue('--rw-x'))).toBeGreaterThan(moved);
    ui.unmount();expect(frame).toBeNull();
  });
});
