import './app.css';
import { mount } from 'svelte';
import App from './App.svelte';
import { applyTheme } from './lib/state.svelte';

applyTheme();

const target = document.getElementById('app');
if (!target) throw new Error('#app element not found');

const app = mount(App, { target });

export default app;
