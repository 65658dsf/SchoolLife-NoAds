package com.amap.api.location;
import android.location.Location;
/** Compile-only API declarations. Never packaged into the APK. */
public class AMapLocation extends Location {
    public AMapLocation(String provider) { super(provider); }
    public AMapLocation(Location source) { super(source); }
    public int getErrorCode() { return 0; }
    public String getErrorInfo() { return ""; }
    public String getAddress() { return ""; }
    public String getPoiName() { return ""; }
    public void setCoordType(String value) {}
    public void setOffset(boolean value) {}
    public void setAdCode(String value) {}
    public void setCityCode(String value) {}
    public void setPoiName(String value) {}
    public void setLocationDetail(String value) {}
    public void setErrorCode(int value) {}
    public void setErrorInfo(String value) {}
    public void setCountry(String value) {}
    public void setProvince(String value) {}
    public void setCity(String value) {}
    public void setDistrict(String value) {}
    public void setStreet(String value) {}
    public void setAddress(String value) {}
}
