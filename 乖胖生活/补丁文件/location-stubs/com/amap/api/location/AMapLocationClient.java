package com.amap.api.location;
import android.content.Context;
/** Compile-only API declarations. Never packaged into the APK. */
public class AMapLocationClient {
    public AMapLocationClient(Context context) throws Exception {}
    public void setLocationListener(AMapLocationListener listener) {}
    public void unRegisterLocationListener(AMapLocationListener listener) {}
    public void startLocation() {}
    public void stopLocation() {}
    public void onDestroy() {}
    public boolean isStarted() { return false; }
}
