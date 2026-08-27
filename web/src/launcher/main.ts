import { mount } from 'svelte'
import '../app.css'
import Launcher from './Launcher.svelte'

mount(Launcher, { target: document.getElementById('app')! })
