package com.qiekj.user.location;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.location.Address;
import android.location.Geocoder;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.widget.Toast;

import com.amap.api.location.AMapLocation;
import com.amap.api.location.AMapLocationClient;
import com.amap.api.location.AMapLocationListener;
import com.amap.api.location.CoordinateConverter;
import com.amap.api.location.DPoint;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.Future;
import java.util.concurrent.SynchronousQueue;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/**
 * One-shot compatibility client for the application's existing one-shot calls.
 * Keeps AMap as the first choice, then obtains a real fix from Android providers.
 * It never supplies a default coordinate, changes a key, or relaxes mock checks.
 */
public final class CompatibleLocationClient extends AMapLocationClient {
    private static final long FALLBACK_DELAY_MS = 6000;
    private static final long TOTAL_TIMEOUT_MS = 25000;
    private static final long GEOCODE_TIMEOUT_MS = 3000;
    private static final long MAX_FIX_AGE_MS = 60000;

    // Geocoder's synchronous Binder call may ignore interruption. Bound the
    // number of blocked workers instead of creating a thread for every retry.
    private static final ThreadPoolExecutor GEOCODERS = new ThreadPoolExecutor(
            0, 2, 30, TimeUnit.SECONDS, new SynchronousQueue<Runnable>(),
            new ThreadFactory() {
                @Override public Thread newThread(Runnable task) {
                    Thread thread = new Thread(task, "LocationAddress");
                    thread.setDaemon(true);
                    return thread;
                }
            });
    private static volatile JSONArray provinceNames;
    private static long lastToastAt;
    private static final ThreadLocal<Boolean> SYSTEM_DELIVERY = new ThreadLocal<Boolean>();
    private static final ThreadLocal<AMapLocation> SYSTEM_LOCATION = new ThreadLocal<AMapLocation>();

    /** Allows the cache patch to avoid inheriting an unrelated old adCode. */
    public static boolean isDeliveringSystemLocation() {
        return Boolean.TRUE.equals(SYSTEM_DELIVERY.get());
    }

    /** The real system fix while its synchronous application listeners run. */
    public static AMapLocation currentSystemLocation() {
        return SYSTEM_LOCATION.get();
    }

    /** Keep SDK diagnostics useful without exposing keys or raw location details. */
    public static String errorMessage(AMapLocation location) {
        if (location == null) return "定位未返回结果，请稍后重试";
        int code = location.getErrorCode();
        String reason;
        switch (code) {
            case 0: return "已获取当前位置";
            case 7: reason = "高德定位授权失败"; break;
            case 12: reason = "未获定位权限，请在系统设置中允许"; break;
            case 4: reason = "定位网络连接失败，请检查网络"; break;
            case 14: reason = "卫星信号不足，请到开阔处重试"; break;
            case 15: reason = "检测到模拟位置，无法用于定位"; break;
            case 20: reason = "定位精度不足，请允许精确位置"; break;
            default: reason = "暂时无法获取当前位置，请稍后重试"; break;
        }
        return reason + "（错误码 " + code + "）";
    }

    private final Context context;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Set<AMapLocationListener> listeners = new LinkedHashSet<AMapLocationListener>();
    private volatile boolean started;
    private boolean destroyed;
    private long generation;
    private boolean fallbackStarted;
    private LocationManager manager;
    private LocationListener systemListener;
    private AMapLocationListener amapListener;
    private AMapLocation lastError;
    private AMapLocation pendingFix;
    private Future<?> geocodeWork;
    private Runnable fallbackTimer;
    private Runnable totalTimer;
    private Runnable geocodeTimer;

    public CompatibleLocationClient(Context context) throws Exception {
        super(context);
        Context application = context.getApplicationContext();
        this.context = application != null ? application : context;
    }

    private void onMain(Runnable action) {
        if (Looper.myLooper() == Looper.getMainLooper()) action.run();
        else main.post(action);
    }

    @Override public void setLocationListener(final AMapLocationListener listener) {
        if (listener == null) return;
        onMain(new Runnable() {
            @Override public void run() {
                if (!destroyed) listeners.add(listener);
            }
        });
    }

    @Override public void unRegisterLocationListener(final AMapLocationListener listener) {
        onMain(new Runnable() {
            @Override public void run() { listeners.remove(listener); }
        });
    }

    @Override public void startLocation() {
        onMain(new Runnable() {
            @Override public void run() { startOnMain(); }
        });
    }

    private void startOnMain() {
        if (destroyed || started) return;
        started = true;
        fallbackStarted = false;
        lastError = null;
        pendingFix = null;
        final long token = ++generation;
        amapListener = new AMapLocationListener() {
            @Override public void onLocationChanged(final AMapLocation location) {
                onMain(new Runnable() {
                    @Override public void run() {
                        if (!current(token)) return;
                        if (location != null && location.getErrorCode() == 0) {
                            complete(token, location, null);
                        } else {
                            if (location != null) lastError = location;
                            startFallback(token);
                        }
                    }
                });
            }
        };
        super.setLocationListener(amapListener);
        fallbackTimer = new Runnable() {
            @Override public void run() { startFallback(token); }
        };
        totalTimer = new Runnable() {
            @Override public void run() {
                if (!current(token)) return;
                if (pendingFix != null) {
                    complete(token, pendingFix, "已获取位置，地址暂时不可用，请稍后重试");
                } else {
                    complete(token, failure("系统定位超时"),
                            "定位失败，请确认系统定位已开启并允许位置权限后重试");
                }
            }
        };
        main.postDelayed(fallbackTimer, FALLBACK_DELAY_MS);
        main.postDelayed(totalTimer, TOTAL_TIMEOUT_MS);
        super.startLocation();
    }

    private boolean current(long token) {
        return started && !destroyed && generation == token;
    }

    private boolean permission(String name) {
        return context.checkCallingOrSelfPermission(name) == PackageManager.PERMISSION_GRANTED;
    }

    private boolean hasLocationPermission() {
        return permission(Manifest.permission.ACCESS_FINE_LOCATION)
                || permission(Manifest.permission.ACCESS_COARSE_LOCATION);
    }

    private void startFallback(final long token) {
        if (!current(token) || fallbackStarted) return;
        fallbackStarted = true;
        if (!hasLocationPermission()) {
            complete(token, failure("未授予位置权限", 12), "请在系统设置中允许胖乖生活访问位置信息");
            return;
        }
        manager = (LocationManager) context.getSystemService(Context.LOCATION_SERVICE);
        if (manager == null) return;
        systemListener = new LocationListener() {
            @Override public void onLocationChanged(final Location location) {
                onMain(new Runnable() {
                    @Override public void run() { useSystemFix(token, location); }
                });
            }
            @Override public void onStatusChanged(String provider, int status, Bundle extras) { }
            @Override public void onProviderEnabled(String provider) { }
            @Override public void onProviderDisabled(String provider) { }
        };
        Location bestCached = null;
        String[] providers = {LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER, "fused"};
        List<String> available;
        try { available = manager.getAllProviders(); }
        catch (RuntimeException unavailable) { return; }
        for (String provider : providers) {
            if (!available.contains(provider)) continue;
            if (LocationManager.GPS_PROVIDER.equals(provider)
                    && !permission(Manifest.permission.ACCESS_FINE_LOCATION)) continue;
            try {
                if (!manager.isProviderEnabled(provider)) continue;
                manager.requestLocationUpdates(provider, 1000L, 0f, systemListener, Looper.getMainLooper());
                Location cached = manager.getLastKnownLocation(provider);
                if (valid(cached) && (bestCached == null || cached.getAccuracy() < bestCached.getAccuracy())) {
                    bestCached = cached;
                }
            } catch (RuntimeException unavailable) {
                // Permission revocation or an unavailable provider does not
                // prevent another provider (or AMap) from completing the fix.
            }
        }
        if (bestCached != null) useSystemFix(token, bestCached);
    }

    private boolean valid(Location location) {
        if (location == null || location.isFromMockProvider()) return false;
        if (!location.hasAccuracy() || !(location.getAccuracy() > 0)
                || Float.isInfinite(location.getAccuracy())) return false;
        double lat = location.getLatitude();
        double lon = location.getLongitude();
        if (Double.isNaN(lat) || Double.isNaN(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return false;
        long fixTime = location.getElapsedRealtimeNanos();
        if (fixTime <= 0) return false;
        long age = SystemClock.elapsedRealtimeNanos() - fixTime;
        return age >= 0 && age <= MAX_FIX_AGE_MS * 1000000L;
    }

    private void useSystemFix(final long token, Location source) {
        if (!current(token) || pendingFix != null || !hasLocationPermission() || !valid(source)) return;
        final Location raw = new Location(source);
        final AMapLocation result;
        try {
            // Android providers return WGS84. The existing application expects
            // AMap's GCJ02 coordinates; never label unconverted coordinates GCJ02.
            boolean offset = CoordinateConverter.isAMapDataAvailable(raw.getLatitude(), raw.getLongitude());
            DPoint point = new CoordinateConverter(context)
                    .from(CoordinateConverter.CoordType.GPS)
                    .coord(new DPoint(raw.getLatitude(), raw.getLongitude())).convert();
            if (point == null || Double.isNaN(point.getLatitude())
                    || Double.isNaN(point.getLongitude())
                    || Math.abs(point.getLatitude()) > 90 || Math.abs(point.getLongitude()) > 180) return;
            result = new AMapLocation(raw);
            result.setLatitude(point.getLatitude());
            result.setLongitude(point.getLongitude());
            result.setCoordType(offset ? "GCJ02" : "WGS84");
            result.setOffset(offset);
            result.setAdCode("");
            result.setCityCode("");
            result.setPoiName("");
            result.setLocationDetail("Android 系统定位");
        } catch (Exception conversionFailed) {
            return;
        }
        pendingFix = result;
        removeSystemUpdates();
        geocodeTimer = new Runnable() {
            @Override public void run() {
                if (current(token)) complete(token, result, "已获取位置，地址暂时不可用，请稍后重试");
            }
        };
        main.postDelayed(geocodeTimer, GEOCODE_TIMEOUT_MS);
        try {
            geocodeWork = GEOCODERS.submit(new Runnable() {
                @Override public void run() {
                    Address address = null;
                    try {
                        if (Geocoder.isPresent()) {
                            List<Address> addresses = new Geocoder(context, Locale.SIMPLIFIED_CHINESE)
                                    .getFromLocation(raw.getLatitude(), raw.getLongitude(), 1);
                            if (addresses != null && !addresses.isEmpty()) address = addresses.get(0);
                        }
                    } catch (Exception unavailable) { }
                    final Address found = address;
                    final String[] names = addressNames(found);
                    main.post(new Runnable() {
                        @Override public void run() {
                            if (!current(token)) return;
                            if (found != null) applyAddress(result, found, names);
                            complete(token, result, found == null
                                    ? "已获取位置，地址暂时不可用，请稍后重试" : null);
                        }
                    });
                }
            });
        } catch (RuntimeException busy) {
            complete(token, result, "已获取位置，地址暂时不可用，请稍后重试");
        }
    }

    /** Names only: the bundled province.json contains no administrative codes. */
    private String[] addressNames(Address address) {
        if (address == null) return new String[] {"", "", ""};
        String province = text(address.getAdminArea());
        String city = text(address.getLocality());
        String district = text(address.getSubLocality());
        if (city.length() == 0) city = text(address.getSubAdminArea());
        try {
            JSONArray all = provinceNames;
            if (all == null) {
                InputStream stream = context.getAssets().open("province.json");
                try {
                    ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                    byte[] buffer = new byte[4096];
                    int count;
                    while ((count = stream.read(buffer)) != -1) bytes.write(buffer, 0, count);
                    all = new JSONArray(new String(bytes.toByteArray(), "UTF-8"));
                    provinceNames = all;
                } finally { stream.close(); }
            }
            for (int p = 0; p < all.length(); p++) {
                JSONObject region = all.getJSONObject(p);
                String knownProvince = region.optString("name");
                if (!sameName(province, knownProvince)) continue;
                province = knownProvince;
                JSONArray cities = region.optJSONArray("city");
                if (cities == null) break;
                for (int c = 0; c < cities.length(); c++) {
                    JSONObject municipality = cities.getJSONObject(c);
                    String knownCity = municipality.optString("name");
                    if (!sameName(city, knownCity)
                            && !(city.length() == 0 && knownCity.equals(knownProvince))) continue;
                    city = knownCity;
                    JSONArray areas = municipality.optJSONArray("area");
                    if (areas != null) for (int d = 0; d < areas.length(); d++) {
                        String knownDistrict = areas.optString(d);
                        if (sameName(district, knownDistrict)) { district = knownDistrict; break; }
                    }
                    break;
                }
                break;
            }
        } catch (Exception unavailable) {
            // Geocoder's names remain usable if this optional old name list
            // cannot be loaded or a district is absent from it.
        }
        return new String[] {province, city, district};
    }

    private static String text(String value) { return value == null ? "" : value.trim(); }

    private static boolean sameName(String one, String two) {
        if (one.length() == 0 || two.length() == 0) return false;
        if (one.equals(two)) return true;
        return one.equals(two.replaceFirst("[省市区县]$", ""))
                || two.equals(one.replaceFirst("[省市区县]$", ""));
    }

    private static void applyAddress(AMapLocation result, Address address, String[] names) {
        result.setCountry(text(address.getCountryName()));
        result.setProvince(names[0]);
        result.setCity(names[1]);
        result.setDistrict(names[2]);
        result.setStreet(text(address.getThoroughfare()));
        String line = address.getMaxAddressLineIndex() >= 0 ? text(address.getAddressLine(0)) : "";
        result.setAddress(line);
        String label = text(address.getFeatureName());
        if (label.length() == 0) label = names[2].length() > 0 ? names[2] : names[1];
        if (label.length() == 0) label = names[0];
        result.setPoiName(label);
        // Address has no Chinese adCode/cityCode fields. In particular, a postal
        // code is not an adCode; retain empty values instead of guessing.
    }

    private AMapLocation failure(String reason) {
        if (lastError != null) return lastError;
        return failure(reason, 8);
    }

    private AMapLocation failure(String reason, int code) {
        AMapLocation error = new AMapLocation("system");
        error.setErrorCode(code);
        error.setErrorInfo(reason);
        error.setLocationDetail(reason);
        return error;
    }

    private void complete(long token, AMapLocation result, String message) {
        if (!current(token)) return;
        if (result.getErrorCode() == 0 && !hasLocationPermission()) {
            result = failure("位置权限已撤销", 12);
            message = "位置权限已撤销，请在系统设置中重新允许";
        }
        boolean systemResult = result == pendingFix && result.getErrorCode() == 0;
        List<AMapLocationListener> recipients = new ArrayList<AMapLocationListener>(listeners);
        stopOnMain();
        if (message != null) {
            long now = SystemClock.elapsedRealtime();
            if (lastToastAt == 0 || now - lastToastAt >= 5000) {
                lastToastAt = now;
                Toast.makeText(context, message, Toast.LENGTH_LONG).show();
            }
        }
        Boolean previous = SYSTEM_DELIVERY.get();
        AMapLocation previousLocation = SYSTEM_LOCATION.get();
        SYSTEM_DELIVERY.set(Boolean.valueOf(systemResult));
        SYSTEM_LOCATION.set(systemResult ? result : null);
        try {
            for (AMapLocationListener recipient : recipients) {
                // A listener may destroy this client or unregister another listener.
                if (destroyed) break;
                if (listeners.contains(recipient)) recipient.onLocationChanged(result);
            }
        } finally {
            if (previous == null) SYSTEM_DELIVERY.remove();
            else SYSTEM_DELIVERY.set(previous);
            if (previousLocation == null) SYSTEM_LOCATION.remove();
            else SYSTEM_LOCATION.set(previousLocation);
        }
    }

    private void removeSystemUpdates() {
        if (manager != null && systemListener != null) {
            try { manager.removeUpdates(systemListener); }
            catch (RuntimeException ignored) { }
        }
        systemListener = null;
    }

    private void stopOnMain() {
        started = false;
        generation++;
        if (fallbackTimer != null) main.removeCallbacks(fallbackTimer);
        if (totalTimer != null) main.removeCallbacks(totalTimer);
        if (geocodeTimer != null) main.removeCallbacks(geocodeTimer);
        fallbackTimer = totalTimer = geocodeTimer = null;
        if (geocodeWork != null) geocodeWork.cancel(true);
        geocodeWork = null;
        pendingFix = null;
        removeSystemUpdates();
        if (amapListener != null) super.unRegisterLocationListener(amapListener);
        amapListener = null;
        super.stopLocation();
    }

    @Override public void stopLocation() {
        onMain(new Runnable() {
            @Override public void run() { stopOnMain(); }
        });
    }

    @Override public boolean isStarted() { return started; }

    @Override public void onDestroy() {
        onMain(new Runnable() {
            @Override public void run() {
                if (destroyed) return;
                destroyed = true;
                stopOnMain();
                listeners.clear();
                CompatibleLocationClient.super.onDestroy();
            }
        });
    }
}
