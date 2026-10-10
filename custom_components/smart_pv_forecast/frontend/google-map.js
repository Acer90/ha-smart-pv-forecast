let loading;
let loadedKey;

export function loadGoogleMaps(key) {
  if (loading && key !== loadedKey) return Promise.reject(new Error("Google Maps: reload the page after changing the API key."));
  if (loading) return loading;
  loadedKey = key;
  loading = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    const callback = `smartPVGoogleReady${Date.now()}`;
    const timeout = setTimeout(() => finish(new Error("Google Maps: loading timed out.")), 20000);
    const finish = (error) => {
      clearTimeout(timeout);
      if (error) window[callback] = () => {};
      else delete window[callback];
      if (error) reject(error); else resolve(window.google.maps);
    };
    window[callback] = () => finish();
    script.onerror = () => finish(new Error("Google Maps: script could not be loaded."));
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&loading=async&callback=${callback}&v=quarterly`;
    script.referrerPolicy = "strict-origin-when-cross-origin";
    document.head.append(script);
  });
  return loading;
}
