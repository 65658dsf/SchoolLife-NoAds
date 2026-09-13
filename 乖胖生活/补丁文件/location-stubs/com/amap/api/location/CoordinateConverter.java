package com.amap.api.location;
import android.content.Context;
/** Compile-only API declarations. */
public class CoordinateConverter {
    public enum CoordType { BAIDU, MAPBAR, MAPABC, SOSOMAP, ALIYUN, GOOGLE, GPS }
    public CoordinateConverter(Context context) {}
    public static boolean isAMapDataAvailable(double latitude, double longitude) { return false; }
    public CoordinateConverter from(CoordType type) { return this; }
    public CoordinateConverter coord(DPoint point) throws Exception { return this; }
    public DPoint convert() throws Exception { return null; }
}
