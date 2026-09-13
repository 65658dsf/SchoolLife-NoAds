"""Compile the actual compatibility sources against deterministic JVM doubles.

These tests cover control flow and cache behavior, not Android hardware, the
actual AMap binary, network geocoding, or the actual coordinate mathematics.
"""
from pathlib import Path
import subprocess
import textwrap

WORKSPACE = Path(__file__).resolve().parent.parent
ROOT = WORKSPACE / ".analysis" / "location-tests"
SOURCES = {
"android/Manifest.java": '''package android; public final class Manifest { public static final class permission { public static final String ACCESS_FINE_LOCATION="fine",ACCESS_COARSE_LOCATION="coarse"; }}''',
"android/content/pm/PackageManager.java": '''package android.content.pm; public class PackageManager { public static final int PERMISSION_GRANTED=0; }''',
"android/content/res/AssetManager.java": '''package android.content.res; public class AssetManager { public java.io.InputStream open(String name) { return new java.io.ByteArrayInputStream("[]".getBytes()); }}''',
"android/content/Context.java": '''package android.content;
public class Context {
 public static final String LOCATION_SERVICE="location"; public boolean allowed=true;
 public final android.location.LocationManager locations=new android.location.LocationManager();
 public Context getApplicationContext(){return this;}
 public int checkCallingOrSelfPermission(String permission){return allowed?0:-1;}
 public Object getSystemService(String service){return locations;}
 public android.content.res.AssetManager getAssets(){return new android.content.res.AssetManager();}
}''',
"android/os/Bundle.java": '''package android.os; public class Bundle {}''',
"android/os/SystemClock.java": '''package android.os; public class SystemClock { public static long now=100000; public static long elapsedRealtime(){return now;} public static long elapsedRealtimeNanos(){return now*1000000L;} }''',
"android/os/Looper.java": '''package android.os; public class Looper { private static final Thread OWNER=Thread.currentThread(); private static final Looper MAIN=new Looper(); public static Looper getMainLooper(){return MAIN;} public static Looper myLooper(){return Thread.currentThread()==OWNER?MAIN:null;} }''',
"android/os/Handler.java": '''package android.os;
import java.util.*;
public class Handler {
 private static final class Event implements Comparable<Event>{long time,id;Runnable task;Handler owner;public int compareTo(Event e){int c=Long.compare(time,e.time);return c!=0?c:Long.compare(id,e.id);}}
 private static final PriorityQueue<Event> queue=new PriorityQueue<Event>(); private static long serial;
 public Handler(Looper looper){}
 public boolean post(Runnable r){return postDelayed(r,0);}
 public boolean postDelayed(Runnable r,long delay){synchronized(queue){Event e=new Event();e.time=SystemClock.now+delay;e.task=r;e.owner=this;e.id=++serial;queue.add(e);}return true;}
 public void removeCallbacks(Runnable r){synchronized(queue){Iterator<Event> i=queue.iterator();while(i.hasNext()){Event e=i.next();if(e.owner==this&&e.task==r)i.remove();}}}
 public static void pump(){while(true){Event e;synchronized(queue){e=queue.peek();if(e==null||e.time>SystemClock.now)return;queue.remove();}e.task.run();}}
 public static void advance(long ms){SystemClock.now+=ms;pump();}
 public static void reset(){synchronized(queue){queue.clear();}SystemClock.now=100000;}
}''',
"android/widget/Toast.java": '''package android.widget; public class Toast {public static final int LENGTH_LONG=1; public static String last; public static Toast makeText(android.content.Context c,String text,int length){last=text;return new Toast();}public void show(){}}''',
"android/location/Location.java": '''package android.location;
public class Location {
 private String provider; private double lat,lon;private float accuracy=10;private long nanos;private boolean mock;
 public Location(String p){provider=p;nanos=android.os.SystemClock.elapsedRealtimeNanos();}
 public Location(Location p){provider=p.provider;lat=p.lat;lon=p.lon;accuracy=p.accuracy;nanos=p.nanos;mock=p.mock;}
 public String getProvider(){return provider;} public double getLatitude(){return lat;} public double getLongitude(){return lon;}
 public void setLatitude(double x){lat=x;}public void setLongitude(double x){lon=x;}
 public boolean hasAccuracy(){return true;}public float getAccuracy(){return accuracy;}public void setAccuracy(float x){accuracy=x;}
 public long getElapsedRealtimeNanos(){return nanos;}public void setElapsedRealtimeNanos(long n){nanos=n;}
 public boolean isFromMockProvider(){return mock;}public void setMock(boolean x){mock=x;}
}''',
"android/location/LocationListener.java": '''package android.location; public interface LocationListener {void onLocationChanged(Location l);void onStatusChanged(String p,int s,android.os.Bundle e);void onProviderEnabled(String p);void onProviderDisabled(String p);}''',
"android/location/LocationManager.java": '''package android.location;
import java.util.*;
public class LocationManager {
 public static final String GPS_PROVIDER="gps",NETWORK_PROVIDER="network";public int requests;public final List<LocationListener> listeners=new ArrayList<LocationListener>();public final Map<String,Location> cached=new HashMap<String,Location>();
 public List<String> getAllProviders(){return Arrays.asList("gps","network");}public boolean isProviderEnabled(String p){return true;}
 public void requestLocationUpdates(String p,long interval,float distance,LocationListener l,android.os.Looper looper){requests++;if(!listeners.contains(l))listeners.add(l);}
 public Location getLastKnownLocation(String p){return cached.get(p);}public void removeUpdates(LocationListener l){listeners.remove(l);}
 public void emit(Location l){for(LocationListener listener:new ArrayList<LocationListener>(listeners))listener.onLocationChanged(l);}
}''',
"android/location/Address.java": '''package android.location; public class Address {public String getAdminArea(){return "测试省";}public String getLocality(){return "测试市";}public String getSubLocality(){return "测试区";}public String getSubAdminArea(){return "";}public String getCountryName(){return "中国";}public String getThoroughfare(){return "测试路";}public int getMaxAddressLineIndex(){return 0;}public String getAddressLine(int n){return "新地址";}public String getFeatureName(){return "新地点";}}''',
"android/location/Geocoder.java": '''package android.location;import java.util.*;public class Geocoder {public static volatile boolean present=true;public static volatile java.util.concurrent.CountDownLatch block; public Geocoder(android.content.Context c,Locale locale){}public static boolean isPresent(){return present;}public List<Address> getFromLocation(double lat,double lon,int n){java.util.concurrent.CountDownLatch gate=block;if(gate!=null)try{gate.await();}catch(InterruptedException cancelled){return Collections.emptyList();}return Collections.singletonList(new Address());}}''',
"org/json/JSONArray.java": '''package org.json; public class JSONArray {public JSONArray(String s){}public int length(){return 0;}public JSONObject getJSONObject(int i){throw new IllegalStateException();}public String optString(int i){return "";}}''',
"org/json/JSONObject.java": '''package org.json; public class JSONObject {public String optString(String k){return "";}public JSONArray optJSONArray(String k){return null;}}''',
"com/amap/api/location/AMapLocation.java": '''package com.amap.api.location;
public class AMapLocation extends android.location.Location {
 private int error;private String address="",poi="",adCode="",cityCode="",coordType="",city="",district=""; public boolean offset;
 public AMapLocation(String p){super(p);} public AMapLocation(android.location.Location l){super(l);}
 public int getErrorCode(){return error;}public void setErrorCode(int c){error=c;}public void setErrorInfo(String s){}public void setLocationDetail(String s){}
 public void setCoordType(String s){coordType=s;}public String getCoordType(){return coordType;}public void setOffset(boolean b){offset=b;}
 public void setAdCode(String s){adCode=s;}public String getAdCode(){return adCode;}public void setCityCode(String s){cityCode=s;}
 public void setPoiName(String s){poi=s;}public String getPoiName(){return poi;}public void setAddress(String s){address=s;}public String getAddress(){return address;}
 public void setCountry(String s){}public void setProvince(String s){}public void setCity(String s){city=s;}public void setDistrict(String s){district=s;}public void setStreet(String s){}
}''',
"com/amap/api/location/AMapLocationListener.java": '''package com.amap.api.location;public interface AMapLocationListener {void onLocationChanged(AMapLocation l);}''',
"com/amap/api/location/AMapLocationClient.java": '''package com.amap.api.location;public class AMapLocationClient {
 public AMapLocationListener sdkListener;public int stops,destroys;
 public AMapLocationClient(android.content.Context c)throws Exception{}
 public void setLocationListener(AMapLocationListener l){sdkListener=l;}public void unRegisterLocationListener(AMapLocationListener l){if(sdkListener==l)sdkListener=null;}
 public void startLocation(){}public void stopLocation(){stops++;}public void onDestroy(){destroys++;}public boolean isStarted(){return false;}
 public void emit(AMapLocation l){if(sdkListener!=null)sdkListener.onLocationChanged(l);}
}''',
"com/amap/api/location/DPoint.java": '''package com.amap.api.location;public class DPoint {private final double lat,lon;public DPoint(double a,double b){lat=a;lon=b;}public double getLatitude(){return lat;}public double getLongitude(){return lon;}}''',
"com/amap/api/location/CoordinateConverter.java": '''package com.amap.api.location;public class CoordinateConverter {
 public enum CoordType{GPS}private DPoint p;public CoordinateConverter(android.content.Context c){}public CoordinateConverter from(CoordType t){return this;}public CoordinateConverter coord(DPoint p){this.p=p;return this;}
 public static boolean isAMapDataAvailable(double lat,double lon){return lon>70&&lon<140&&lat>0&&lat<60;}
 public DPoint convert(){return isAMapDataAvailable(p.getLatitude(),p.getLongitude())?new DPoint(p.getLatitude()+.01,p.getLongitude()+.02):p;}
}''',
"com/qiekj/user/ui/amap/UserLocation.java": '''package com.qiekj.user.ui.amap;public class UserLocation {
 private final double lat,lon;private final String code,address,name,distance;
 public UserLocation(double a,double b,String c,String d,String e,String f){lat=a;lon=b;code=c;address=d;name=e;distance=f;}
 public double getLatitude(){return lat;}public double getLongitude(){return lon;}public String getAdCode(){return code;}public String getAddress(){return address;}public String getName(){return name;}public String getDistance(){return distance;}
}''',
"BehaviorTest.java": '''
import android.content.Context;import android.os.Handler;import android.os.SystemClock;import android.location.Location;import android.location.LocationListener;
import com.amap.api.location.*;import com.qiekj.user.location.*;import com.qiekj.user.ui.amap.UserLocation;import java.util.*;
public class BehaviorTest {
 static int groups;static void check(boolean pass,String what){if(!pass)throw new AssertionError(what);}
 static void clean(){Handler.reset();check(!CompatibleLocationClient.isDeliveringSystemLocation(),"flag leaked");check(CompatibleLocationClient.currentSystemLocation()==null,"result leaked");}
 static Location raw(){Location p=new Location("gps");p.setLatitude(30);p.setLongitude(120);return p;}
 static AMapLocation error(int code){AMapLocation p=new AMapLocation("amap");p.setErrorCode(code);return p;}
 static void await(List<?> list)throws Exception {long end=System.nanoTime()+2000000000L;while(list.isEmpty()&&System.nanoTime()<end){Handler.pump();Thread.sleep(1);}check(!list.isEmpty(),"worker did not complete");}
 static void ok(String name){groups++;System.out.println("PASS "+name);}
 static UserLocation old(){return new UserLocation(39.903179,116.397755,"110101","旧北京地址","旧地点","旧距离");}
 public static void main(String[] args)throws Exception {
  clean();Context a=new Context();CompatibleLocationClient c=new CompatibleLocationClient(a);List<AMapLocation> r=new ArrayList<AMapLocation>();
  c.setLocationListener(x->{check(!CompatibleLocationClient.isDeliveringSystemLocation(),"SDK marked system");check(CompatibleLocationClient.currentSystemLocation()==null,"SDK system record");UserLocation old=old();check(LocationCacheBridge.normalize(old)==old,"SDK cache changed");r.add(x);});
  c.startLocation();AMapLocationListener stale=c.sdkListener;AMapLocation sdk=new AMapLocation("amap");sdk.setLatitude(20);c.emit(sdk);stale.onLocationChanged(sdk);Handler.advance(30000);check(r.size()==1&&r.get(0)==sdk,"SDK success duplicated");check(a.locations.requests==0,"unnecessary fallback");ok("SDK success once and unchanged cache");

  clean();Context b=new Context();CompatibleLocationClient d=new CompatibleLocationClient(b);List<AMapLocation> out=new ArrayList<AMapLocation>();
  d.setLocationListener(x->{check(x.getErrorCode()==0,"fallback error");check(CompatibleLocationClient.isDeliveringSystemLocation(),"missing system flag");check(CompatibleLocationClient.currentSystemLocation()==x,"wrong current result");UserLocation n=LocationCacheBridge.normalize(old());check(n.getAdCode().equals(""),"old adCode retained");check(n.getAddress().equals("新地址")&&n.getName().equals("新地点")&&n.getDistance().equals(""),"old address retained");check(Math.abs(n.getLatitude()-30.01)<.0001&&Math.abs(n.getLongitude()-120.02)<.0001,"converted coordinate not used");out.add(x);});
  d.startLocation();d.emit(error(7));check(out.isEmpty()&&b.locations.requests==2,"error7 not delayed for fallback");b.locations.emit(raw());await(out);Handler.advance(30000);check(out.size()==1,"system success duplicated");clean();ok("error7 to real system fix, address/cache/ThreadLocal");

  Context denied=new Context();denied.allowed=false;CompatibleLocationClient e=new CompatibleLocationClient(denied);List<AMapLocation> deniedResults=new ArrayList<AMapLocation>();e.setLocationListener(deniedResults::add);e.startLocation();e.emit(error(7));check(deniedResults.size()==1&&deniedResults.get(0).getErrorCode()==12,"permission did not return12");check(denied.locations.requests==0,"requested without permission");ok("missing permission returns12");

  clean();Context stopped=new Context();CompatibleLocationClient f=new CompatibleLocationClient(stopped);List<AMapLocation> late=new ArrayList<AMapLocation>();f.setLocationListener(late::add);f.startLocation();AMapLocationListener oldSdk=f.sdkListener;f.emit(error(7));LocationListener oldSystem=stopped.locations.listeners.get(0);f.stopLocation();oldSdk.onLocationChanged(new AMapLocation("amap"));oldSystem.onLocationChanged(raw());Handler.advance(30000);check(late.isEmpty(),"stop emitted stale result");f.startLocation();oldSdk.onLocationChanged(new AMapLocation("amap"));check(late.isEmpty(),"old generation emitted after restart");AMapLocationListener dying=f.sdkListener;f.onDestroy();dying.onLocationChanged(new AMapLocation("amap"));Handler.advance(30000);check(late.isEmpty()&&f.destroys==1,"destroy emitted result");ok("stop/destroy/restart reject stale callbacks");

  clean();Context invalid=new Context();Location mock=raw();mock.setMock(true);Location expired=raw();expired.setElapsedRealtimeNanos((SystemClock.now-61000)*1000000L);invalid.locations.cached.put("gps",mock);invalid.locations.cached.put("network",expired);CompatibleLocationClient g=new CompatibleLocationClient(invalid);List<AMapLocation> rejected=new ArrayList<AMapLocation>();g.setLocationListener(rejected::add);g.startLocation();g.emit(error(7));invalid.locations.emit(mock);invalid.locations.emit(expired);Handler.advance(3000);check(rejected.isEmpty(),"invalid fix accepted");Handler.advance(22000);check(rejected.size()==1&&rejected.get(0).getErrorCode()==7,"timeout not real SDK error");ok("mock and expired cache rejected, timeout error retained");

  clean();Context silent=new Context();CompatibleLocationClient h=new CompatibleLocationClient(silent);List<AMapLocation> timeout=new ArrayList<AMapLocation>();h.setLocationListener(timeout::add);h.startLocation();Handler.advance(5999);check(silent.locations.requests==0,"fallback too early");Handler.advance(1);check(silent.locations.requests==2,"6s fallback missing");Handler.advance(19000);check(timeout.size()==1&&timeout.get(0).getErrorCode()!=0,"25s timeout fabricated success");ok("6s fallback / 25s total timeout");

  clean();Context cached=new Context();Location recent=raw();recent.setElapsedRealtimeNanos((SystemClock.now-59000)*1000000L);cached.locations.cached.put("gps",recent);CompatibleLocationClient i=new CompatibleLocationClient(cached);List<AMapLocation> fresh=new ArrayList<AMapLocation>();i.setLocationListener(fresh::add);i.startLocation();i.emit(error(7));await(fresh);check(fresh.get(0).getErrorCode()==0,"fresh cache rejected");ok("59s real cached fix accepted");

  clean();Context failing=new Context();CompatibleLocationClient j=new CompatibleLocationClient(failing);List<Boolean> entered=new ArrayList<Boolean>();j.setLocationListener(x->{check(CompatibleLocationClient.isDeliveringSystemLocation(),"exception callback flag absent");entered.add(true);throw new IllegalStateException("intentional listener failure");});j.startLocation();j.emit(error(7));boolean caught=false;try{failing.locations.emit(raw());await(entered);}catch(IllegalStateException expected){caught=true;}check(caught,"listener exception swallowed or missing");clean();ok("ThreadLocal restored when listener throws");

  String message=CompatibleLocationClient.errorMessage(error(7));check(message.contains("授权失败")&&!message.contains("权限"),"error7 mislabeled permission");check(CompatibleLocationClient.errorMessage(error(12)).contains("权限"),"error12 unclear");ok("safe error messages distinguish key authorization and permission");

  clean();android.location.Geocoder.block=new java.util.concurrent.CountDownLatch(1);Context slow=new Context();CompatibleLocationClient k=new CompatibleLocationClient(slow);List<AMapLocation> limited=new ArrayList<AMapLocation>();k.setLocationListener(x->{UserLocation n=LocationCacheBridge.normalize(old());check(n.getAdCode().isEmpty()&&n.getAddress().isEmpty()&&n.getName().isEmpty(),"failed geocoder retained old address");limited.add(x);});k.startLocation();k.emit(error(7));slow.locations.emit(raw());Handler.advance(2999);check(limited.isEmpty(),"geocoder did not get its time budget");Handler.advance(1);check(limited.size()==1&&limited.get(0).getErrorCode()==0,"3s geocoder timeout lost real fix");android.location.Geocoder.block.countDown();android.location.Geocoder.block=null;Thread.sleep(20);Handler.pump();check(limited.size()==1,"late geocoder duplicated callback");clean();ok("3s geocoder timeout keeps real fix and clears obsolete address");
  System.out.println("ALL "+groups+" TEST GROUPS PASSED");
 }
}''',
}


def main():
    src = ROOT / "src"
    classes = ROOT / "classes"
    classes.mkdir(parents=True, exist_ok=True)
    files = []
    for name, body in SOURCES.items():
        target = src / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(body), encoding="utf-8")
        files.append(str(target))
    real_sources = WORKSPACE / "补丁文件/location-src/com/qiekj/user/location"
    files.extend(str(real_sources / name) for name in ("CompatibleLocationClient.java", "LocationCacheBridge.java"))
    subprocess.run(["javac", "--release", "8", "-encoding", "UTF-8", "-d", str(classes), *files], check=True)
    run = subprocess.run(["java", "-cp", str(classes), "BehaviorTest"], capture_output=True, encoding="utf-8")
    report = run.stdout + run.stderr
    (ROOT / "results.txt").write_text(report, encoding="utf-8")
    print(report)
    raise SystemExit(run.returncode)


if __name__ == "__main__":
    main()
