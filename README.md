# Managing the data deluge (take 2)

Totally using this as an excuse to use a readme.

I've set up a little workflow to filter all potentially relevant material, which is very much cobbled together and could be streamlined quite a lot. 

The folder consists of 2 python files. The first one fetches papers by using an API call, and the second displays them in a database. 

### Set-up

1. We set up a virtual python environment for the relevant packages. To this end, use the `requirements.txt` file to create either a virtualenv, conda or otherwise. See [here](https://packaging.python.org/en/latest/guides/installing-using-pip-and-virtual-environments/) or [here](https://docs.conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html). Mine is called **arxiv** so adjust this in your .sh file accordingly. 

2. To save time and do everything at once, I've created `move-fetch-display.sh` which does all tasks at once. 
    a. Adapt the `.sh` file, line 4, to fit your venv.
    b. Make the `.sh` file executible by running 
```
chmod +x move-fetch-display.sh
```
Now it can be executed using `./`, see below. 
which will a) activate the virtualenv, b) check if there's a current `.db` file then rename and move it to `past-papers/` with the creation date, c) make an api call and then d) serve it all locally. 

3. Make the past-papers folder using 
```
mkdir past-papers
```

4.  Tailor the `config.json` file to your own needs by adjusting the following: 
- *days_back* : How many days into the past to look up
- *categories* : Which categories in the arxiv taxonomy to query. 
- *keywords* : Reads the abstract and extracts keywords you're interested in 
- *authors* : Matches author surnames -- if there are specific academics (like your supervisor, or friends) whose new preprints you don't want to miss. (Note, you would have to also be following the right arxiv categories)

5. And finally, run the file by using
```
./move-fetch-display.sh
```


