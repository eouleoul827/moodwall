import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ScreenBoundary } from './App.jsx';

vi.mock('./display/display-diagnostics.js', () => ({ reportDisplayRenderFailure: vi.fn(async () => {}) }));
function Broken() { throw new Error('PRIVATE_ERROR_DETAIL'); }
afterEach(() => vi.restoreAllMocks());
describe('route error boundaries', () => {
  it('keeps rendering failures quiet for the public wall', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<ScreenBoundary quiet><Broken /></ScreenBoundary>);
    expect(screen.getByRole('main', { name: '무드월' })).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull(); expect(screen.queryByText(/PRIVATE_ERROR_DETAIL|다시 불러|연결을 확인/)).toBeNull();
  });
  it('preserves actionable failure UI for student and operator routes', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<ScreenBoundary><Broken /></ScreenBoundary>);
    expect(screen.getByRole('button', { name: '다시 불러오기' })).toBeTruthy(); expect(screen.queryByText('PRIVATE_ERROR_DETAIL')).toBeNull();
  });
});
