package com.qiekj.user.location;

import com.amap.api.location.AMapLocation;
import com.qiekj.user.ui.amap.UserLocation;

/** Update the location record even when an original callback only copies coordinates. */
public final class LocationCacheBridge {
    private LocationCacheBridge() {}
    public static UserLocation normalize(UserLocation original) {
        AMapLocation actual = CompatibleLocationClient.currentSystemLocation();
        if (actual == null) return original;
        return new UserLocation(actual.getLatitude(), actual.getLongitude(), "",
                text(actual.getAddress()), text(actual.getPoiName()), "");
    }
    private static String text(String value) { return value == null ? "" : value; }
}
