import { afterEach, describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '@/App';
import { useSettingsStore } from '@/stores/settingsStore';
import { chartOptions, chartPalette } from '@/components/charts/chartTheme';
import { renderWithProviders } from './render';

afterEach(() => {
  useSettingsStore.getState().update({ theme: 'light' });
  document.documentElement.classList.remove('dark');
});

describe('dark theme', () => {
  it('starts light and the top-right button switches to dark and back, saving the choice', async () => {
    renderWithProviders(<App />);
    const toggle = await screen.findByRole('button', { name: 'Switch to dark theme' });
    expect(document.documentElement).not.toHaveClass('dark');

    await userEvent.click(toggle);
    expect(document.documentElement).toHaveClass('dark');
    expect(useSettingsStore.getState().theme).toBe('dark');
    expect(JSON.parse(localStorage.getItem('aurum.settings')!).state.theme).toBe('dark');

    await userEvent.click(screen.getByRole('button', { name: 'Switch to light theme' }));
    expect(document.documentElement).not.toHaveClass('dark');
  });

  it('sits in the top-right cluster next to Search', async () => {
    renderWithProviders(<App />);
    const search = await screen.findByRole('button', { name: /Search pages and instruments/ });
    expect(search.nextElementSibling).toBe(screen.getByTestId('theme-toggle'));
  });

  it('the Settings page offers the same choice', async () => {
    renderWithProviders(<App />, { route: '/settings' });
    expect((await screen.findAllByText('Application theme')).length).toBeGreaterThan(0);
    useSettingsStore.getState().update({ theme: 'dark' });
    expect(await screen.findByRole('button', { name: 'Switch to light theme' })).toBeInTheDocument();
    expect(document.documentElement).toHaveClass('dark');
  });

  it('canvas charts get a dark background and readable text in dark mode', () => {
    const dark = chartOptions('dark');
    expect(dark.layout?.background).toMatchObject({ color: chartPalette('dark').background });
    expect(dark.layout?.textColor).toBe('#98A1AB');
    expect(chartOptions('light').layout?.background).toMatchObject({ color: '#FFFFFF' });
  });
});
