import java.io.File;
import jadx.api.JadxArgs;
import jadx.api.JadxDecompiler;

class DecompileApp {
    public static void main(String[] argv) throws Exception {
        JadxArgs args = new JadxArgs();
        args.setInputFile(new File(argv[0]));
        args.setOutDir(new File(argv[1]));
        args.setSkipResources(true);
        args.setThreadsCount(8);
        args.setClassFilter(name -> name.equals("com.qiekj.App") || name.startsWith("com.qiekj.user.") || name.startsWith("com.qiekeji.pgad."));
        try (JadxDecompiler jadx = new JadxDecompiler(args)) {
            System.out.println("Loading APK");
            jadx.load();
            System.out.println("Saving application packages");
            jadx.save();
            System.out.println("Done, errors=" + jadx.getErrorsCount());
        }
    }
}
