// Injected into every page before it loads. It defines the object
// web/src/launcher/bridge.ts looks for, so the launcher never imports a Tauri
// package and still renders in a plain browser via browserBridge. It is
// injected on navigation to the Python server too, which is what lets the app
// tier offer Change library.
(function () {
  if (!window.__TAURI__ || !window.__TAURI__.core) return
  const invoke = window.__TAURI__.core.invoke
  window.__SPLITSTEP_BRIDGE__ = {
    home: () => invoke('home'),
    known: () => invoke('known'),
    configured: () => invoke('configured'),
    pickFolder: () => invoke('pick_folder'),
    freeSpace: (path) => invoke('free_space', { path }),
    hasLibrary: (path) => invoke('has_library', { path }),
    open: (path, create) => invoke('open_library', { path, create }),
    backToChooser: () => invoke('back_to_chooser'),
  }
})()
